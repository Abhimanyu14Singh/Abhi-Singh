using System;
using System.Threading.Tasks;

namespace SkyFrame.Exchange.Client
{
    /// <summary>
    /// Keyed, single-flight async cache for "fetch when inputs change" components (SF Connect, SF Results).
    /// The solver calls <see cref="Request"/> on every solve; a fetch starts only when the key changed
    /// and nothing is in flight; the completion callback (worker thread) lets the component expire
    /// itself so the next solve reads <see cref="Value"/>. Never blocks the caller.
    /// </summary>
    public sealed class AsyncCache<T>
    {
        private readonly object _gate = new object();
        private string _inFlightKey;
        private string _wantedKey;
        private Func<Task<T>> _wantedFactory;

        public string Key { get; private set; }
        public T Value { get; private set; }
        public Exception Error { get; private set; }
        public DateTime Updated { get; private set; }
        public bool Loading { get { lock (_gate) return _inFlightKey != null; } }

        /// <summary>True when <see cref="Value"/>/<see cref="Error"/> belong to <paramref name="key"/>.</summary>
        public bool IsCurrent(string key) { lock (_gate) return Key == key; }

        /// <summary>Starts a fetch for <paramref name="key"/> if needed. Returns true if a fetch was started or queued.</summary>
        public bool Request(string key, Func<Task<T>> factory, Action onCompleted, bool force = false)
        {
            lock (_gate)
            {
                if (!force && (key == Key || key == _inFlightKey)) return false;
                if (_inFlightKey != null)
                {
                    // remember only the newest wish; it starts when the current fetch ends
                    _wantedKey = key;
                    _wantedFactory = factory;
                    return true;
                }
                Start(key, factory, onCompleted);
                return true;
            }
        }

        // caller holds _gate
        private void Start(string key, Func<Task<T>> factory, Action onCompleted)
        {
            _inFlightKey = key;
            Task.Run(async () =>
            {
                T value = default(T);
                Exception error = null;
                try { value = await factory().ConfigureAwait(false); }
                catch (Exception ex) { error = ex; }
                lock (_gate)
                {
                    Key = key;
                    Value = error == null ? value : default(T);
                    Error = error;
                    Updated = DateTime.Now;
                    _inFlightKey = null;
                    if (_wantedKey != null)
                    {
                        var k = _wantedKey; var f = _wantedFactory;
                        _wantedKey = null; _wantedFactory = null;
                        if (k != Key) Start(k, f, onCompleted);
                    }
                }
                try { onCompleted?.Invoke(); } catch { /* UI callback failures must not escape */ }
            });
        }

        public void Reset()
        {
            lock (_gate)
            {
                Key = null; Value = default(T); Error = null; _wantedKey = null; _wantedFactory = null;
            }
        }
    }
}
