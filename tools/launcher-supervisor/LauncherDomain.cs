using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace Sword.LauncherSupervisor;

internal sealed class ContractException(string code) : Exception(code)
{
    public string Code { get; } = code;
}

internal sealed record StartPolicy(string DuplicateStart, string OptionalAbsent);
internal sealed record StopPolicy(bool Idempotent, string Residue);
internal sealed record StartSpec(string AdapterId, string AbsentBehavior, IReadOnlyList<string> LegacySpecIds);
internal sealed record ReadinessSpec(string ProbeId, string Success, bool DegradedAllowed);
internal sealed record PortSpec(string Ownership, string PortMode, string Transport, int? LoopbackPort, string? EndpointRef);
internal sealed record StopSpec(string AdapterId, int GracefulTimeoutMs, string Escalation);
internal sealed record ServiceSpec(
    string ServiceId,
    string? PublicReadinessId,
    IReadOnlyList<string> Dependencies,
    string Requirement,
    string Ownership,
    bool LegacyProfileMember,
    StartSpec Start,
    ReadinessSpec Readiness,
    int ReadyDeadlineMs,
    PortSpec Port,
    StopSpec Stop);

internal sealed record ServiceGraph(
    string SchemaVersion,
    string ProfileId,
    StartPolicy StartPolicy,
    StopPolicy StopPolicy,
    IReadOnlyList<ServiceSpec> Services,
    string Sha256);

internal static class GraphLoader
{
    private static readonly Regex IdPattern = new("^[a-z][a-z0-9_-]{0,63}$", RegexOptions.CultureInvariant);

    public static ServiceGraph Load(string path)
    {
        var bytes = File.ReadAllBytes(path);
        using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions
        {
            AllowTrailingCommas = false,
            CommentHandling = JsonCommentHandling.Disallow,
            MaxDepth = 32,
        });
        var root = document.RootElement;
        Exact(root, "schema_version", "profile_id", "start_policy", "stop_policy", "services");
        var schemaVersion = RequiredString(root, "schema_version");
        if (schemaVersion != "launcher_service_graph.v1") throw new ContractException("graph_schema_version_invalid");
        var profileId = RequiredId(root, "profile_id");

        var start = root.GetProperty("start_policy");
        Exact(start, "duplicate_start", "optional_absent");
        var startPolicy = new StartPolicy(RequiredString(start, "duplicate_start"), RequiredString(start, "optional_absent"));
        if (startPolicy != new StartPolicy("join_active", "continue_degraded")) throw new ContractException("graph_start_policy_invalid");

        var stop = root.GetProperty("stop_policy");
        Exact(stop, "idempotent", "residue");
        var stopPolicy = new StopPolicy(RequiredBoolean(stop, "idempotent"), RequiredString(stop, "residue"));
        if (stopPolicy != new StopPolicy(true, "retain_fixed_state")) throw new ContractException("graph_stop_policy_invalid");

        var servicesElement = root.GetProperty("services");
        if (servicesElement.ValueKind != JsonValueKind.Array || servicesElement.GetArrayLength() == 0)
            throw new ContractException("graph_services_invalid");
        var services = servicesElement.EnumerateArray().Select(ParseService).ToArray();
        ValidateServices(services);
        return new ServiceGraph(schemaVersion, profileId, startPolicy, stopPolicy, services, Sha256(bytes));
    }

    private static ServiceSpec ParseService(JsonElement element)
    {
        Exact(element,
            "service_id", "public_readiness_id", "dependencies", "requirement", "ownership",
            "legacy_profile_member", "start", "readiness", "ready_deadline_ms", "port", "stop");
        var serviceId = RequiredId(element, "service_id");
        var publicElement = element.GetProperty("public_readiness_id");
        var publicId = publicElement.ValueKind == JsonValueKind.Null ? null : RequiredId(element, "public_readiness_id");
        var dependenciesElement = element.GetProperty("dependencies");
        if (dependenciesElement.ValueKind != JsonValueKind.Array) throw new ContractException("graph_dependencies_invalid");
        var dependencies = dependenciesElement.EnumerateArray().Select(item => RequiredId(item)).ToArray();

        var start = element.GetProperty("start");
        Exact(start, "adapter_id", "absent_behavior", "legacy_spec_ids");
        var legacySpecElement = start.GetProperty("legacy_spec_ids");
        if (legacySpecElement.ValueKind != JsonValueKind.Array) throw new ContractException("graph_legacy_specs_invalid");
        var startSpec = new StartSpec(
            RequiredId(start, "adapter_id"),
            RequiredEnum(start, "absent_behavior", "fail", "optional_absent", "external_probe_only"),
            legacySpecElement.EnumerateArray().Select(RequiredId).ToArray());

        var readiness = element.GetProperty("readiness");
        Exact(readiness, "probe_id", "success", "degraded_allowed");
        var readinessSpec = new ReadinessSpec(
            RequiredId(readiness, "probe_id"),
            RequiredEnum(readiness, "success", "owned_identity_and_probe", "external_probe", "module_status"),
            RequiredBoolean(readiness, "degraded_allowed"));

        var port = element.GetProperty("port");
        Exact(port, "ownership", "port_mode", "transport", "loopback_port", "endpoint_ref");
        var endpointElement = port.GetProperty("endpoint_ref");
        var endpointRef = endpointElement.ValueKind == JsonValueKind.Null ? null : RequiredId(port, "endpoint_ref");
        var portSpec = new PortSpec(
            RequiredEnum(port, "ownership", "owned", "external", "none"),
            RequiredEnum(port, "port_mode", "manifest_default", "launcher_default", "none"),
            RequiredEnum(port, "transport", "http", "websocket", "none"),
            RequiredNullableInteger(port, "loopback_port", 1, 65535),
            endpointRef);

        var stop = element.GetProperty("stop");
        Exact(stop, "adapter_id", "graceful_timeout_ms", "escalation");
        var stopSpec = new StopSpec(
            RequiredId(stop, "adapter_id"),
            RequiredInteger(stop, "graceful_timeout_ms", 0, 120000),
            RequiredEnum(stop, "escalation", "owned_only", "none"));

        return new ServiceSpec(
            serviceId,
            publicId,
            dependencies,
            RequiredEnum(element, "requirement", "required", "optional", "external"),
            RequiredEnum(element, "ownership", "owned", "external"),
            RequiredBoolean(element, "legacy_profile_member"),
            startSpec,
            readinessSpec,
            RequiredInteger(element, "ready_deadline_ms", 1, 300000),
            portSpec,
            stopSpec);
    }

    private static void ValidateServices(IReadOnlyList<ServiceSpec> services)
    {
        var ids = new HashSet<string>(StringComparer.Ordinal);
        var publicIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var service in services)
        {
            if (!ids.Add(service.ServiceId)) throw new ContractException("graph_service_duplicate");
            if (service.PublicReadinessId is not null && !publicIds.Add(service.PublicReadinessId))
                throw new ContractException("graph_public_id_duplicate");
            if (service.Dependencies.Count != service.Dependencies.Distinct(StringComparer.Ordinal).Count())
                throw new ContractException("graph_dependency_duplicate");
            if (service.Start.LegacySpecIds.Count != service.Start.LegacySpecIds.Distinct(StringComparer.Ordinal).Count())
                throw new ContractException("graph_legacy_specs_duplicate");
            var noPort = service.Port.Ownership == "none";
            if (noPort != (service.Port.EndpointRef is null) ||
                noPort != (service.Port.LoopbackPort is null) ||
                noPort != (service.Port.Transport == "none") ||
                noPort != (service.Port.PortMode == "none"))
                throw new ContractException("graph_port_semantics_invalid");

            if (service.Requirement == "external" || service.Ownership == "external")
            {
                if (service.Requirement != "external" || service.Ownership != "external" || service.LegacyProfileMember ||
                    service.Start.AdapterId != "external_probe_only" || service.Start.AbsentBehavior != "external_probe_only" ||
                    service.Start.LegacySpecIds.Count != 0 || service.Readiness.Success != "external_probe" || service.Readiness.DegradedAllowed ||
                    service.Port.Ownership != "external" || service.Port.PortMode != "launcher_default" || noPort ||
                    service.Stop.AdapterId != "external_noop" || service.Stop.GracefulTimeoutMs != 0 || service.Stop.Escalation != "none")
                    throw new ContractException("graph_external_semantics_invalid");
                continue;
            }

            if (service.Ownership != "owned" || !service.LegacyProfileMember || service.Start.AdapterId != "legacy_stack_script" ||
                !service.Start.LegacySpecIds.Contains(service.ServiceId, StringComparer.Ordinal) ||
                service.Stop.AdapterId is not ("legacy_owned_pid" or "legacy_owned_pid_tree") || service.Stop.Escalation != "owned_only" ||
                service.Port.Ownership == "external" || (!noPort && service.Port.PortMode != "manifest_default"))
                throw new ContractException("graph_owned_semantics_invalid");

            if (service.Requirement == "required")
            {
                if (service.Start.AbsentBehavior != "fail" || service.Readiness.DegradedAllowed)
                    throw new ContractException("graph_required_semantics_invalid");
            }
            else if (service.Requirement == "optional")
            {
                if (service.Start.AbsentBehavior != "optional_absent" || !service.Readiness.DegradedAllowed ||
                    service.Readiness.Success != "owned_identity_and_probe" || noPort)
                    throw new ContractException("graph_optional_semantics_invalid");
            }
            else
            {
                throw new ContractException("graph_requirement_invalid");
            }

            if ((service.Readiness.Success == "module_status") != noPort ||
                (!noPort && service.Readiness.Success != "owned_identity_and_probe"))
                throw new ContractException("graph_readiness_port_semantics_invalid");
        }

        foreach (var service in services)
        {
            foreach (var dependency in service.Dependencies)
            {
                if (!ids.Contains(dependency)) throw new ContractException("graph_dependency_unknown");
                if (dependency == service.ServiceId) throw new ContractException("graph_dependency_cycle");
            }
        }
        _ = TopologicalOrder(services);
    }

    public static IReadOnlyList<string> TopologicalOrder(IReadOnlyList<ServiceSpec> services)
    {
        var remaining = services.ToDictionary(item => item.ServiceId, item => new HashSet<string>(item.Dependencies, StringComparer.Ordinal), StringComparer.Ordinal);
        var result = new List<string>(services.Count);
        while (remaining.Count > 0)
        {
            var ready = remaining.Where(item => item.Value.Count == 0).Select(item => item.Key).OrderBy(item => item, StringComparer.Ordinal).ToArray();
            if (ready.Length == 0) throw new ContractException("graph_dependency_cycle");
            foreach (var id in ready)
            {
                result.Add(id);
                remaining.Remove(id);
                foreach (var dependencies in remaining.Values) dependencies.Remove(id);
            }
        }
        return result;
    }

    private static void Exact(JsonElement element, params string[] expected)
    {
        if (element.ValueKind != JsonValueKind.Object) throw new ContractException("graph_object_invalid");
        var actual = element.EnumerateObject().Select(item => item.Name).OrderBy(item => item, StringComparer.Ordinal).ToArray();
        var wanted = expected.OrderBy(item => item, StringComparer.Ordinal).ToArray();
        if (!actual.SequenceEqual(wanted, StringComparer.Ordinal)) throw new ContractException("graph_unknown_or_missing_field");
    }

    private static string RequiredString(JsonElement element, string property)
    {
        var value = element.GetProperty(property);
        if (value.ValueKind != JsonValueKind.String || string.IsNullOrEmpty(value.GetString())) throw new ContractException("graph_string_invalid");
        return value.GetString()!;
    }

    private static string RequiredId(JsonElement element, string property) => RequiredId(element.GetProperty(property));
    private static string RequiredId(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.String || !IdPattern.IsMatch(element.GetString() ?? string.Empty))
            throw new ContractException("graph_id_invalid");
        return element.GetString()!;
    }

    private static string RequiredEnum(JsonElement element, string property, params string[] allowed)
    {
        var value = RequiredString(element, property);
        if (!allowed.Contains(value, StringComparer.Ordinal)) throw new ContractException("graph_enum_invalid");
        return value;
    }

    private static bool RequiredBoolean(JsonElement element, string property)
    {
        var value = element.GetProperty(property);
        if (value.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) throw new ContractException("graph_boolean_invalid");
        return value.GetBoolean();
    }

    private static int RequiredInteger(JsonElement element, string property, int minimum, int maximum)
    {
        var value = element.GetProperty(property);
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out var number) || number < minimum || number > maximum)
            throw new ContractException("graph_integer_invalid");
        return number;
    }

    private static int? RequiredNullableInteger(JsonElement element, string property, int minimum, int maximum)
    {
        var value = element.GetProperty(property);
        if (value.ValueKind == JsonValueKind.Null) return null;
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out var number) || number < minimum || number > maximum)
            throw new ContractException("graph_integer_invalid");
        return number;
    }

    internal static string Sha256(ReadOnlySpan<byte> bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
}

internal sealed record BindingBody(
    [property: JsonPropertyName("binding_version")] string BindingVersion,
    [property: JsonPropertyName("profile_id")] string ProfileId,
    [property: JsonPropertyName("graph_sha256")] string GraphSha256,
    [property: JsonPropertyName("graph_schema_sha256")] string GraphSchemaSha256,
    [property: JsonPropertyName("operation_schema_sha256")] string OperationSchemaSha256,
    [property: JsonPropertyName("service_order")] IReadOnlyList<string> ServiceOrder,
    [property: JsonPropertyName("public_readiness_ids")] IReadOnlyList<string> PublicReadinessIds,
    [property: JsonPropertyName("required_service_ids")] IReadOnlyList<string> RequiredServiceIds,
    [property: JsonPropertyName("optional_service_ids")] IReadOnlyList<string> OptionalServiceIds,
    [property: JsonPropertyName("external_service_ids")] IReadOnlyList<string> ExternalServiceIds);

internal sealed record BindingDocument(
    [property: JsonPropertyName("binding_sha256")] string BindingSha256,
    [property: JsonPropertyName("binding")] BindingBody Binding);

internal static class BindingGenerator
{
    private static readonly JsonSerializerOptions Compact = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private static readonly JsonSerializerOptions Indented = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, WriteIndented = true };

    public static string Render(string repositoryRoot, ServiceGraph graph)
    {
        var graphSchema = Path.Combine(repositoryRoot, "contracts", "launcher", "launcher-service-graph.v1.schema.json");
        var operationSchema = Path.Combine(repositoryRoot, "contracts", "launcher", "launcher-operation.v1.schema.json");
        var body = new BindingBody(
            "launcher_service_graph.binding.v1",
            graph.ProfileId,
            graph.Sha256,
            GraphLoader.Sha256(File.ReadAllBytes(graphSchema)),
            GraphLoader.Sha256(File.ReadAllBytes(operationSchema)),
            GraphLoader.TopologicalOrder(graph.Services),
            graph.Services.Where(item => item.PublicReadinessId is not null).Select(item => item.PublicReadinessId!).OrderBy(item => item, StringComparer.Ordinal).ToArray(),
            graph.Services.Where(item => item.Requirement == "required").Select(item => item.ServiceId).OrderBy(item => item, StringComparer.Ordinal).ToArray(),
            graph.Services.Where(item => item.Requirement == "optional").Select(item => item.ServiceId).OrderBy(item => item, StringComparer.Ordinal).ToArray(),
            graph.Services.Where(item => item.Requirement == "external").Select(item => item.ServiceId).OrderBy(item => item, StringComparer.Ordinal).ToArray());
        var hash = GraphLoader.Sha256(JsonSerializer.SerializeToUtf8Bytes(body, Compact));
        return JsonSerializer.Serialize(new BindingDocument(hash, body), Indented).Replace("\r\n", "\n", StringComparison.Ordinal) + "\n";
    }
}

internal static partial class DriftValidator
{
    public static void Validate(string repositoryRoot, ServiceGraph graph)
    {
        var owned = graph.Services.Where(item => item.Ownership == "owned").ToDictionary(item => item.ServiceId, StringComparer.Ordinal);
        foreach (var service in owned.Values)
        {
            var path = Path.Combine(repositoryRoot, "ops", "manifests", "services", service.ServiceId + ".json");
            if (!File.Exists(path)) throw new ContractException("drift_service_manifest_missing");
            using var document = JsonDocument.Parse(File.ReadAllBytes(path));
            var root = document.RootElement;
            if (root.GetProperty("service_id").GetString() != service.ServiceId) throw new ContractException("drift_service_id");
            var dependencies = root.GetProperty("depends_on").EnumerateArray().Select(item => item.GetString()!).OrderBy(item => item, StringComparer.Ordinal);
            if (!dependencies.SequenceEqual(service.Dependencies.OrderBy(item => item, StringComparer.Ordinal), StringComparer.Ordinal))
                throw new ContractException("drift_service_dependencies");
            var health = root.GetProperty("health");
            if (service.Port.Ownership == "none")
            {
                if (health.GetProperty("type").GetString() != "module_status")
                    throw new ContractException("drift_service_port");
            }
            else
            {
                if (!Uri.TryCreate(health.GetProperty("url").GetString(), UriKind.Absolute, out var healthUri) || !healthUri.IsLoopback)
                    throw new ContractException("drift_service_port");
                var transport = healthUri.Scheme switch
                {
                    "http" or "https" => "http",
                    "ws" or "wss" => "websocket",
                    _ => "unknown",
                };
                if (service.Port.PortMode != "manifest_default" || service.Port.LoopbackPort != healthUri.Port || service.Port.Transport != transport)
                    throw new ContractException("drift_service_port");
            }
        }

        var profilePath = Path.Combine(repositoryRoot, "ops", "manifests", "profiles", graph.ProfileId + ".json");
        using (var profile = JsonDocument.Parse(File.ReadAllBytes(profilePath)))
        {
            var actual = profile.RootElement.GetProperty("services").EnumerateArray().Select(item => item.GetString()!).OrderBy(item => item, StringComparer.Ordinal);
            var expected = graph.Services.Where(item => item.LegacyProfileMember).Select(item => item.ServiceId).OrderBy(item => item, StringComparer.Ordinal);
            if (!actual.SequenceEqual(expected, StringComparer.Ordinal)) throw new ContractException("drift_legacy_profile_membership");
        }

        using (var route = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(repositoryRoot, "contracts", "turn", "ordinary-standard-route.v1.json"))))
        {
            var actual = route.RootElement.GetProperty("readiness").GetProperty("expected_service_ids").EnumerateArray().Select(item => item.GetString()!).OrderBy(item => item, StringComparer.Ordinal);
            var expected = graph.Services.Where(item => item.PublicReadinessId is not null).Select(item => item.PublicReadinessId!).OrderBy(item => item, StringComparer.Ordinal);
            if (!actual.SequenceEqual(expected, StringComparer.Ordinal)) throw new ContractException("drift_ordinary_route_readiness");
        }

        var server = File.ReadAllText(Path.Combine(repositoryRoot, "tools", "home-control-launcher", "server.js"));
        var voicevox = graph.Services.Single(item => item.ServiceId == "voicevox");
        var voicevoxMatch = VoicevoxDefaultPortRegex().Match(server);
        if (!voicevoxMatch.Success || !int.TryParse(voicevoxMatch.Groups[1].Value, out var voicevoxPort) ||
            voicevox.Port.PortMode != "launcher_default" || voicevox.Port.Transport != "http" || voicevox.Port.LoopbackPort != voicevoxPort)
            throw new ContractException("drift_voicevox_port");
        var functionStart = server.IndexOf("const expectedServicesForOptions", StringComparison.Ordinal);
        var functionEnd = server.IndexOf("const startupReadyTimeoutMsForService", functionStart, StringComparison.Ordinal);
        if (functionStart < 0 || functionEnd <= functionStart) throw new ContractException("drift_launcher_function_missing");
        var function = server[functionStart..functionEnd];
        var launcherIds = ServicePushRegex().Matches(function).Select(match => match.Groups[1].Value).OrderBy(item => item, StringComparer.Ordinal);
        var graphPublicIds = graph.Services.Where(item => item.PublicReadinessId is not null).Select(item => item.PublicReadinessId!).OrderBy(item => item, StringComparer.Ordinal);
        if (!launcherIds.SequenceEqual(graphPublicIds, StringComparer.Ordinal)) throw new ContractException("drift_launcher_readiness_list");

        var stack = File.ReadAllText(Path.Combine(repositoryRoot, "ops", "scripts", "home-control-stack", "start-home-control-stack.ps1"));
        var stackIds = ServiceSpecRegex().Matches(stack).Select(match => match.Groups[1].Value).Distinct(StringComparer.Ordinal).OrderBy(item => item, StringComparer.Ordinal);
        var expectedStackIds = graph.Services.SelectMany(item => item.Start.LegacySpecIds).Distinct(StringComparer.Ordinal).OrderBy(item => item, StringComparer.Ordinal);
        if (!stackIds.SequenceEqual(expectedStackIds, StringComparer.Ordinal))
            throw new ContractException("drift_stack_service_list");
    }

    [GeneratedRegex("services\\.push\\('([^']+)'\\)", RegexOptions.CultureInvariant)]
    private static partial Regex ServicePushRegex();

    [GeneratedRegex("\\$specs\\s*\\+=\\s*New-ServiceSpec[\\s\\S]{0,400}?-Name\\s+\"([^\"]+)\"", RegexOptions.CultureInvariant)]
    private static partial Regex ServiceSpecRegex();

    [GeneratedRegex("let\\s+voicevoxPort\\s*=\\s*(\\d+)", RegexOptions.CultureInvariant)]
    private static partial Regex VoicevoxDefaultPortRegex();
}
