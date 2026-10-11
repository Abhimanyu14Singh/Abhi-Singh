// Debounced, coalescing, non-blocking sender used by SF Send. It has no UI
// dependency: the Grasshopper component submits payloads from the solver thread
// and gets a callback (on a worker thread) when a response arrives; the component
// then marshals to the UI thread and expires itself.

using System;
using System.Threading;
using System.Threading.Tasks;
using SkyFrame.Exchange.Json;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Exchange.Client
{
    public sealed class SendRequest
    {
        public ExchangePayload Payload { get; set; }
        public bool Preview { get; set; }
        public ApplyMode Mode { get; set; } = ApplyMode.Merge;
        public bool Run { get; set; }
        public JsonValue Mapping { get; set; }

        /// <summary>Identity of the request (payload content + options) for change detection.</summary>
        public string Key => _key ?? (_key = $"{Payload?.ContentHash()}|{Preview}|{Mode}|{Run}|{Mapping?.ToJson()}");
        private string _key;
    }

    public sealed class SendOutcome
    {
        public SendRequest Request { get; set; }
        public PreviewResult Preview { get; set; }
        public ApplyResult Apply { get; set; }
        public Exception Error { get; set; }
        public DateTime Finished { get; set; }
        public bool Ok => Error == null;
    }

    public sealed class LiveSender : IDisposable
    {
        private readonly object _gate = new object();
        private readonly Func<SkyFrameClient> _clientFactory;
        private Timer _timer;
        private SendRequest _pending;
        private bool _inFlight;
        private string _lastSentKey;
        private string _lastAttemptKey;
        private int _generation;
        private bool _disposed;

        public LiveSender(Func<SkyFrameClient> clientFactory, TimeSpan debounce)
        {
            _clientFactory = clientFactory ?? throw new ArgumentNullException(nameof(clientFactory));
            Debounce = debounce;
        }

        public TimeSpan Debounce { get; set; }

        /// <summary>Raised on a worker thread after every completed request.</summary>
        public event Action<SendOutcome> Completed;

        public SendOutcome LastOutcome { get; private set; }

        public bool Busy { get { lock (_gate) return _inFlight || _timer != null; } }

        /// <summary>Key of the last request that completed successfully.</summary>
        public string LastSentKey { get { lock (_gate) return _lastSentKey; } }

        /// <summary>
        /// Queues a request. With <paramref name="immediate"/> false it is debounced: every call
        /// within <see cref="Debounce"/> restarts the wait and replaces the queued payload.
        /// If a request is in flight the newest one is sent right after it (older ones are dropped).
        /// Returns false (and does nothing) when the request equals the last one attempted or the one
        /// already queued, unless <paramref name="force"/> is set — so a failed send is not retried in a
        /// loop by the re-solve that reports it; the user retries with the Send button.
        /// </summary>
        public bool Submit(SendRequest req, bool immediate, bool force = false)
        {
            lock (_gate)
            {
                if (_disposed) return false;
                var key = req.Key;
                if (!force && (key == _lastAttemptKey || key == _pending?.Key)) return false;
                _pending = req;
                _generation++;
                _timer?.Dispose();
                _timer = null;
                if (immediate || Debounce <= TimeSpan.Zero) { StartIfIdle(); return true; }
                int gen = _generation;
                _timer = new Timer(_ => OnTimer(gen), null, Debounce, Timeout.InfiniteTimeSpan);
                return true;
            }
        }

        public void Cancel()
        {
            lock (_gate)
            {
                _pending = null;
                _timer?.Dispose();
                _timer = null;
            }
        }

        private void OnTimer(int gen)
        {
            lock (_gate)
            {
                if (gen != _generation || _disposed) return;
                _timer?.Dispose();
                _timer = null;
                StartIfIdle();
            }
        }

        // caller holds _gate
        private void StartIfIdle()
        {
            if (_inFlight || _pending == null) return;
            var req = _pending;
            _pending = null;
            _inFlight = true;
            _lastAttemptKey = req.Key;
            Task.Run(() => RunAsync(req));
        }

        private async Task RunAsync(SendRequest req)
        {
            var outcome = new SendOutcome { Request = req };
            try
            {
                var client = _clientFactory();
                if (req.Preview) outcome.Preview = await client.PreviewAsync(req.Payload, req.Mapping).ConfigureAwait(false);
                else outcome.Apply = await client.ApplyAsync(req.Payload, req.Mode, req.Run, req.Mapping).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                outcome.Error = ex;
            }
            outcome.Finished = DateTime.Now;
            lock (_gate)
            {
                _inFlight = false;
                if (outcome.Ok) _lastSentKey = req.Key;
                LastOutcome = outcome;
            }
            try { Completed?.Invoke(outcome); } catch { /* never let a UI callback kill the sender */ }
            lock (_gate)
            {
                if (_timer == null) StartIfIdle();
            }
        }

        public void Dispose()
        {
            lock (_gate)
            {
                _disposed = true;
                _pending = null;
                _timer?.Dispose();
                _timer = null;
            }
        }
    }
}
