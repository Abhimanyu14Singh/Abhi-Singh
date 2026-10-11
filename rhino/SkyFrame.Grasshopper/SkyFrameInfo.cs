using System;
using System.Collections.Concurrent;
using System.Drawing;
using System.Reflection;
using Grasshopper.Kernel;

namespace SkyFrame.Grasshopper
{
    public sealed class SkyFrameInfo : GH_AssemblyInfo
    {
        public override string Name => "SkyFrame";
        public override Bitmap Icon => IconLoader.Get("SkyFrame");
        public override string Description => "Live link between Grasshopper/Rhino and the SkyFrame building analysis app (skyframe.exchange/1).";
        public override Guid Id => new Guid("5b6f2c1e-7d3a-4f0b-9a8e-3c2d1e0f5a71");
        public override string AuthorName => "SkyFrame";
        public override string AuthorContact => "https://github.com/Abhimanyu14Singh/Abhi-Singh";
        public override string Version => typeof(SkyFrameInfo).Assembly.GetName().Version.ToString(3);
    }

    /// <summary>Loads the embedded 24×24 PNG icons.</summary>
    internal static class IconLoader
    {
        private static readonly ConcurrentDictionary<string, Bitmap> Cache = new ConcurrentDictionary<string, Bitmap>();

        public static Bitmap Get(string name) => Cache.GetOrAdd(name, n =>
        {
            try
            {
                var s = Assembly.GetExecutingAssembly().GetManifestResourceStream($"SkyFrame.Grasshopper.Icons.{n}.png");
                return s == null ? null : new Bitmap(s);
            }
            catch
            {
                return null;
            }
        });
    }
}
