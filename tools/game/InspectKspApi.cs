// Metadata-only reflection: never instantiate game types or invoke methods.
using System;
using System.IO;
using System.Reflection;

class InspectKspApi
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
        Assembly assembly = Assembly.ReflectionOnlyLoadFrom(Path.Combine(directory, "Assembly-CSharp.dll"));
        foreach (string name in new string[] {"FlightDriver", "ShipConstruction", "Game", "FlightState", "EditorLogic", "EditorDriver", "VesselCrewManifest"})
        {
            Type type = assembly.GetType(name, true);
            if (name == "VesselCrewManifest")
                foreach (ConstructorInfo constructor in type.GetConstructors())
                    Console.WriteLine(name + ": " + constructor);
            foreach (MethodInfo method in type.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance | BindingFlags.DeclaredOnly))
                if (name == "VesselCrewManifest" || method.Name.Contains("Start") || method.Name.IndexOf("launch", StringComparison.OrdinalIgnoreCase) >= 0 || method.Name == "LoadShip")
                {
                    Console.WriteLine(name + ": " + method);
                    Console.WriteLine("  parameters: " + string.Join(", ", Array.ConvertAll(method.GetParameters(), p => p.ParameterType.Name + " " + p.Name)));
                }
        }
    }
}
