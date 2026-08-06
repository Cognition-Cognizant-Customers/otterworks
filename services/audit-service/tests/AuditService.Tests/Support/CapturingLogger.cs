using Microsoft.Extensions.Logging;

namespace AuditService.Tests.Support;

public sealed record LogEntry(LogLevel Level, string Message, Exception? Exception);

/// <summary>
/// Minimal <see cref="ILogger{T}"/> that records what was logged, so middleware and consumer
/// behaviour can be asserted without a live logging provider.
/// </summary>
public sealed class CapturingLogger<T> : ILogger<T>
{
    private readonly List<LogEntry> _entries = new();
    private readonly object _gate = new();

    public IReadOnlyList<LogEntry> Entries
    {
        get
        {
            lock (_gate)
            {
                return _entries.ToList();
            }
        }
    }

    public IDisposable? BeginScope<TState>(TState state) where TState : notnull => NullScope.Instance;

    public bool IsEnabled(LogLevel logLevel) => true;

    public void Log<TState>(
        LogLevel logLevel,
        EventId eventId,
        TState state,
        Exception? exception,
        Func<TState, Exception?, string> formatter)
    {
        lock (_gate)
        {
            _entries.Add(new LogEntry(logLevel, formatter(state, exception), exception));
        }
    }

    public bool HasLevel(LogLevel level) => Entries.Any(e => e.Level == level);

    private sealed class NullScope : IDisposable
    {
        public static readonly NullScope Instance = new();

        public void Dispose()
        {
        }
    }
}
