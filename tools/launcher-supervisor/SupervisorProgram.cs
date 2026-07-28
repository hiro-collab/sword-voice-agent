namespace Sword.LauncherSupervisor;

internal static class Paths
{
    public static string Graph(string root) => Path.Combine(root, "ops", "manifests", "launcher-service-graph.standard.v1.json");
    public static string Binding(string root) => Path.Combine(root, "contracts", "launcher", "generated", "launcher-service-graph.standard.v1.binding.json");
}

internal static class SupervisorProgram
{
    public static Task<int> RunAsync(string[] args)
    {
        try
        {
            if (args.Length != 2) throw new ContractException("command_invalid");
            var command = args[0];
            var repositoryRoot = Path.GetFullPath(args[1]);
            var graph = GraphLoader.Load(Paths.Graph(repositoryRoot));
            return Task.FromResult(command switch
            {
                "render-binding" => Render(repositoryRoot, graph),
                "validate" => Validate(repositoryRoot, graph),
                "self-test" => SelfTests.Run(repositoryRoot),
                _ => throw new ContractException("command_invalid"),
            });
        }
        catch (ContractException exception)
        {
            Console.Error.WriteLine("ERROR " + exception.Code);
            return Task.FromResult(1);
        }
        catch
        {
            Console.Error.WriteLine("ERROR internal_failure");
            return Task.FromResult(1);
        }
    }

    private static int Render(string repositoryRoot, ServiceGraph graph)
    {
        Console.Write(BindingGenerator.Render(repositoryRoot, graph));
        return 0;
    }

    private static int Validate(string repositoryRoot, ServiceGraph graph)
    {
        DriftValidator.Validate(repositoryRoot, graph);
        var expected = BindingGenerator.Render(repositoryRoot, graph);
        var actual = File.ReadAllText(Paths.Binding(repositoryRoot)).Replace("\r\n", "\n", StringComparison.Ordinal);
        if (actual != expected) throw new ContractException("binding_stale_or_drifted");
        Console.WriteLine("VALIDATION_CLEAR");
        return 0;
    }
}
