namespace TokenScope.Core.SessionLogs;

/// <summary>
/// Locates Claude Code session logs on disk. Cross-platform: uses
/// <see cref="Environment.SpecialFolder.UserProfile"/> for the home
/// directory and <see cref="Path.Combine"/> for path construction.
///
/// Layout assumed:
/// <code>
///   {home}/.claude/projects/{encoded-project-path}/{session-uuid}.jsonl
/// </code>
/// </summary>
public static class SessionLogDiscovery
{
    public const string ClaudeDirectoryName = ".claude";
    public const string ProjectsDirectoryName = "projects";
    public const string SessionFileExtension = ".jsonl";

    public static string GetProjectsDirectory(string? homeOverride = null)
    {
        var home = homeOverride ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (string.IsNullOrEmpty(home))
        {
            throw new InvalidOperationException(
                "Could not determine the user's home directory.");
        }
        return Path.Combine(home, ClaudeDirectoryName, ProjectsDirectoryName);
    }

    public static IEnumerable<string> EnumerateProjectDirectories(string? projectsDir = null)
    {
        var dir = projectsDir ?? GetProjectsDirectory();
        if (!Directory.Exists(dir))
        {
            return Array.Empty<string>();
        }
        return Directory.EnumerateDirectories(dir);
    }

    public static IEnumerable<string> EnumerateSessionFiles(string projectDir)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(projectDir);
        if (!Directory.Exists(projectDir))
        {
            return Array.Empty<string>();
        }
        return Directory.EnumerateFiles(projectDir, "*" + SessionFileExtension, SearchOption.TopDirectoryOnly);
    }

    public static IEnumerable<string> EnumerateAllSessionFiles(string? projectsDir = null)
    {
        foreach (var project in EnumerateProjectDirectories(projectsDir))
        {
            foreach (var file in EnumerateSessionFiles(project))
            {
                yield return file;
            }
        }
    }
}
