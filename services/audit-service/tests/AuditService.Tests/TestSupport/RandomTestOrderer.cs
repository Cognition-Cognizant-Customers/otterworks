using AuditService.Tests.TestSupport;
using Xunit.Abstractions;
using Xunit.Sdk;

[assembly: TestCaseOrderer("AuditService.Tests.TestSupport.RandomTestCaseOrderer", "AuditService.Tests")]
[assembly: TestCollectionOrderer("AuditService.Tests.TestSupport.RandomTestCollectionOrderer", "AuditService.Tests")]

namespace AuditService.Tests.TestSupport;

/// <summary>
/// Runs test cases and collections in a shuffled order so that any accidental ordering
/// dependence fails fast. Set <c>XUNIT_ORDER_SEED</c> to replay a specific shuffle.
/// </summary>
internal static class RandomOrder
{
    public static readonly int Seed =
        int.TryParse(Environment.GetEnvironmentVariable("XUNIT_ORDER_SEED"), out var configured)
            ? configured
            : Environment.TickCount;

    public static IEnumerable<T> Shuffle<T>(IEnumerable<T> items)
    {
        var random = new Random(Seed);
        return items.OrderBy(_ => random.Next()).ToList();
    }
}

public sealed class RandomTestCaseOrderer : ITestCaseOrderer
{
    public IEnumerable<TTestCase> OrderTestCases<TTestCase>(IEnumerable<TTestCase> testCases)
        where TTestCase : ITestCase => RandomOrder.Shuffle(testCases);
}

public sealed class RandomTestCollectionOrderer : ITestCollectionOrderer
{
    public IEnumerable<ITestCollection> OrderTestCollections(IEnumerable<ITestCollection> testCollections) =>
        RandomOrder.Shuffle(testCollections);
}
