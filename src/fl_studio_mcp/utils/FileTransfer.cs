using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

// A real OLE file source. FL does not accept WM_DROPFILES for every destination.
internal static class FileTransfer
{
    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr context);

    [STAThread]
    private static int Main(string[] args)
    {
        // Enforce the same boundary if this helper is started outside its Python wrapper.
        if (!String.Equals(Environment.GetEnvironmentVariable("FL_MCP_INTERACTION_POLICY"),
                           "interactive", StringComparison.Ordinal))
        {
            Console.Error.WriteLine("OLE file transfer requires FL_MCP_INTERACTION_POLICY=interactive.");
            return 3;
        }
        if (args.Length != 4 || !Path.IsPathRooted(args[0]) || !File.Exists(args[0]))
            return 2;
        int x, y;
        if (!Int32.TryParse(args[2], out x) || !Int32.TryParse(args[3], out y))
            return 2;
        SetProcessDpiAwarenessContext(new IntPtr(-4));
        Application.EnableVisualStyles();
        var deadline = DateTime.UtcNow.AddSeconds(60);
        using (var form = new Form {
            Text = args[1], StartPosition = FormStartPosition.Manual,
            Location = new Point(x, y), Size = new Size(270, 100),
            AutoScaleMode = AutoScaleMode.None, TopMost = true,
            FormBorderStyle = FormBorderStyle.FixedToolWindow,
            MaximizeBox = false, MinimizeBox = false, ShowInTaskbar = false
        })
        using (var label = new Label {
            Text = Path.GetFileName(args[0]), Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleCenter, AutoEllipsis = true
        })
        using (var timer = new Timer { Interval = 60000 })
        {
            bool started = false;
            form.Controls.Add(label);
            form.Shown += delegate {
                Console.WriteLine("FORM " + form.Handle.ToInt64());
                Console.Out.Flush();
            };
            label.QueryContinueDrag += delegate(object sender, QueryContinueDragEventArgs e) {
                if (DateTime.UtcNow >= deadline)
                    e.Action = DragAction.Cancel;
            };
            label.MouseDown += delegate(object sender, MouseEventArgs e) {
                if (started || e.Button != MouseButtons.Left)
                    return;
                started = true;
                try {
                    var data = new DataObject(DataFormats.FileDrop, new string[] { args[0] });
                    var effect = label.DoDragDrop(data, DragDropEffects.Copy);
                    Console.WriteLine("EFFECT " + effect.ToString());
                    Console.Out.Flush();
                }
                catch (Exception error) {
                    Console.WriteLine("ERROR " + error.GetType().Name);
                    Console.Out.Flush();
                }
                finally { form.Close(); }
            };
            timer.Tick += delegate { form.Close(); };
            timer.Start();
            Application.Run(form);
        }
        return 0;
    }
}
