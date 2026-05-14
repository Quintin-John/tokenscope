using System.Collections.Immutable;
using System.Reflection;
using Microsoft.Extensions.Configuration;

namespace TokenScope.Collector.Configuration;

/// <summary>
/// Walks an <see cref="IConfiguration"/> tree and reports keys that don't
/// correspond to any property on the typed options graph. Error messages
/// use the full dotted path so duplicates of common leaf names
/// (e.g. <c>path</c>) are unambiguous.
/// </summary>
internal static class StrictKeyValidator
{
    public static ImmutableArray<string> FindUnknownKeys<T>(IConfiguration config)
        where T : class
    {
        var expected = CollectExpectedKeys(typeof(T));
        var unknown = ImmutableArray.CreateBuilder<string>();
        FindUnknown(config, parentPath: "", expected, unknown);
        return unknown.ToImmutable();
    }

    private static void FindUnknown(
        IConfiguration node,
        string parentPath,
        HashSet<string> expected,
        ImmutableArray<string>.Builder unknown)
    {
        foreach (var child in node.GetChildren())
        {
            var path = string.IsNullOrEmpty(parentPath) ? child.Key : $"{parentPath}.{child.Key}";

            if (IsArrayIndex(child.Key))
            {
                // Array elements bind to the same type as their parent; skip
                // index segments when checking against the expected key set
                // and recurse with the parent path unchanged.
                FindUnknown(child, parentPath, expected, unknown);
                continue;
            }

            if (!expected.Contains(path))
            {
                // Strict within known sections, lenient at root.
                // The Host's DOTNET_-prefix env-var provider injects keys like
                // CLI_TELEMETRY_OPTOUT and RUNNING_IN_CONTAINER at the root of
                // the IConfiguration tree. We can't strip that provider
                // without breaking host internals that rely on DOTNET_*
                // settings, so we treat unknown top-level keys as ambient
                // noise instead. Typos inside a known section (e.g.
                // session_logs.scan_recursive) still get caught with their
                // full dotted path in the error message.
                if (!string.IsNullOrEmpty(parentPath))
                {
                    unknown.Add(path);
                }
                continue;
            }

            FindUnknown(child, path, expected, unknown);
        }
    }

    private static HashSet<string> CollectExpectedKeys(Type root)
    {
        var keys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        CollectKeys(root, parentPath: "", keys, visited: new HashSet<Type>());
        return keys;
    }

    private static void CollectKeys(Type type, string parentPath, HashSet<string> keys, HashSet<Type> visited)
    {
        if (!visited.Add(type))
        {
            return;
        }

        foreach (var prop in type.GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            var name = prop.GetCustomAttribute<ConfigurationKeyNameAttribute>()?.Name
                       ?? prop.Name;
            var path = string.IsNullOrEmpty(parentPath) ? name : $"{parentPath}.{name}";
            keys.Add(path);

            var propType = Nullable.GetUnderlyingType(prop.PropertyType) ?? prop.PropertyType;
            if (ShouldRecurseInto(propType))
            {
                CollectKeys(propType, path, keys, visited);
            }
        }

        visited.Remove(type);
    }

    private static bool ShouldRecurseInto(Type type)
    {
        if (type.IsPrimitive)
        {
            return false;
        }
        if (type == typeof(string)
            || type == typeof(decimal)
            || type == typeof(DateTime)
            || type == typeof(DateTimeOffset)
            || type == typeof(TimeSpan)
            || type == typeof(Guid))
        {
            return false;
        }
        if (type.IsEnum)
        {
            return false;
        }
        return true;
    }

    private static bool IsArrayIndex(string key) =>
        key.Length > 0 && key.All(char.IsDigit);
}
