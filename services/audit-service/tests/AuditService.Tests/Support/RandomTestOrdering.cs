using System.Globalization;
using AuditService.Tests.Support;
using Xunit.Abstractions;
using Xunit.Sdk;

[assembly: TestCaseOrderer("AuditService.Tests.Support.RandomTestCaseOrderer", "AuditService.Tests")]
[assembly: TestCollectionOrderer("AuditService.Tests.Support.RandomTestCollectionOrderer", "AuditService.Tests")]

namespace AuditService.Tests.Support;

/// <summary>
/// Shuffles execution order so ordering dependencies between cases surface immediately.
/// The permutation is derived from <c>AUDIT_TEST_SEED</c>, or from a fresh random seed when
/// that variable is unset. The effective seed is announced once per run (as an xUnit
/// diagnostic message and on stdout), so even a default randomised run can be replayed with
/// <c>AUDIT_TEST_SEED=&lt;announced value&gt; dotnet test</c>.
/// </summary>
internal static class TestOrderSeed
{
    private static int _announced;

    public static int Value { get; } =
        int.TryParse(
            Environment.GetEnvironmentVariable("AUDIT_TEST_SEED"),
            NumberStyles.Integer,
            CultureInfo.InvariantCulture,
            out var seed)
            ? seed
            : Random.Shared.Next();

    public static void Announce(IMessageSink? diagnostics)
    {
        if (Interlocked.Exchange(ref _announced, 1) != 0)
        {
            return;
        }

        var seedText = Value.ToString(CultureInfo.InvariantCulture);
        var message =
            $"Test order is randomised with AUDIT_TEST_SEED={seedText}. " +
            $"Re-run with AUDIT_TEST_SEED={seedText} to reproduce this exact ordering.";

        diagnostics?.OnMessage(new DiagnosticMessage(message));
        Console.WriteLine(message);
    }

    public static uint Shuffle(string key)
    {
        unchecked
        {
            var hash = 2166136261u ^ (uint)Value;
            foreach (var c in key)
            {
                hash = (hash ^ c) * 16777619u;
            }

            return hash;
        }
    }
}

public sealed class RandomTestCaseOrderer : ITestCaseOrderer
{
    public RandomTestCaseOrderer()
        : this(null)
    {
    }

    public RandomTestCaseOrderer(IMessageSink? diagnostics) => TestOrderSeed.Announce(diagnostics);

    public IEnumerable<TTestCase> OrderTestCases<TTestCase>(IEnumerable<TTestCase> testCases)
        where TTestCase : ITestCase =>
        testCases
            .OrderBy(tc => TestOrderSeed.Shuffle(tc.UniqueID))
            .ThenBy(tc => tc.UniqueID, StringComparer.Ordinal)
            .ToList();
}

public sealed class RandomTestCollectionOrderer : ITestCollectionOrderer
{
    public RandomTestCollectionOrderer()
        : this(null)
    {
    }

    public RandomTestCollectionOrderer(IMessageSink? diagnostics) => TestOrderSeed.Announce(diagnostics);

    public IEnumerable<ITestCollection> OrderTestCollections(IEnumerable<ITestCollection> testCollections) =>
        testCollections
            .OrderBy(tc => TestOrderSeed.Shuffle(tc.UniqueID.ToString()))
            .ThenBy(tc => tc.UniqueID)
            .ToList();
}
