// Metadata inspection only; no game type is instantiated or executed.
using System;
using System.IO;
using System.Reflection;
class InspectEditorApi
{
    static string directory;
    static Assembly Resolve(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name;
        string path = Path.Combine(directory, name + ".dll");
        return File.Exists(path) ? Assembly.ReflectionOnlyLoadFrom(path) : Assembly.ReflectionOnlyLoad(args.Name);
    }
    static void Main(string[] args)
    {
        directory = args[0];
        AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve += Resolve;
        Assembly a = Assembly.ReflectionOnlyLoadFrom(Path.Combine(directory, "Assembly-CSharp.dll"));
        foreach (string name in new string[] {"ShipConstruct", "ShipConstruction", "EditorLogic", "EditorDriver", "Part", "GameEvents", "PartModule"})
        {
            Type t = a.GetType(name, true);
            foreach (MethodInfo m in t.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
                if (name == "EditorDriver" || name == "EditorLogic" || m.Name.IndexOf("load", StringComparison.OrdinalIgnoreCase) >= 0 || m.Name.IndexOf("mass", StringComparison.OrdinalIgnoreCase) >= 0 || m.Name.IndexOf("save", StringComparison.OrdinalIgnoreCase) >= 0)
                    Console.WriteLine(name + " METHOD " + m);
            foreach (FieldInfo f in t.GetFields(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
                if ((name == "GameEvents" && f.Name.IndexOf("Editor", StringComparison.OrdinalIgnoreCase) >= 0)
                    || f.Name.IndexOf("mass", StringComparison.OrdinalIgnoreCase) >= 0
                    || f.Name.IndexOf("ship", StringComparison.OrdinalIgnoreCase) >= 0 || f.Name.IndexOf("craft", StringComparison.OrdinalIgnoreCase) >= 0
                    || f.Name.IndexOf("uid", StringComparison.OrdinalIgnoreCase) >= 0)
                    Console.WriteLine(name + " FIELD " + f.FieldType + " " + f.Name);
            foreach (PropertyInfo p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
                if (name == "ShipConstruct" || p.Name.IndexOf("craft", StringComparison.OrdinalIgnoreCase) >= 0)
                    Console.WriteLine(name + " PROPERTY " + p.PropertyType + " " + p.Name);
        }
    }
}
