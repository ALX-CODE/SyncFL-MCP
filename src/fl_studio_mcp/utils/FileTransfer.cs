using System;

// Retired physical OLE source. Native loading uses native/background_bridge.
internal static class FileTransfer
{
    private static int Main(string[] args)
    {
        Console.Error.WriteLine("SyncFL is backend-only; physical file dragging is unavailable.");
        return 3;
    }
}
