using Microsoft.Extensions.Logging;

namespace AuditService.Tests.TestSupport;

internal sealed record LogEntry(LogLevel Level, string Message, Exception? Exception);

/// <summary>In-memory <see cref="ILogger{T}"/> so tests can assert on what was logged.</summary>
internal sealed class RecordingLogger<T> : ILogger<T>
{
    private readonly List<LogEntry> _entries = new();

    public IReadOnlyList<LogEntry> Entries
    {
        get
        {
            lock (_entries)
            {
                return _entries.ToList();
            }
        }
    }

    public IDisposable? BeginScope<TState>(TState state)
        where TState : notnull => null;

    public bool IsEnabled(LogLevel logLevel) => true;

    public void Log<TState>(
        LogLevel logLevel,
        EventId eventId,
        TState state,
        Exception? exception,
        Func<TState, Exception?, string> formatter)
    {
        lock (_entries)
        {
            _entries.Add(new LogEntry(logLevel, formatter(state, exception), exception));
        }
    }
}
