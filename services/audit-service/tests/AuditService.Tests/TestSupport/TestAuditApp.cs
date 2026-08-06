using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Moq;
using OtterWorks.AuditService.Controllers;
using OtterWorks.AuditService.Middleware;
using IAuditServiceContract = OtterWorks.AuditService.Services.IAuditService;

namespace AuditService.Tests.TestSupport;

/// <summary>
/// Hosts the real audit endpoint map and middleware pipeline on an in-memory
/// <see cref="TestServer"/> with a mocked <see cref="IAuditServiceContract"/>.
/// The pipeline order mirrors Program.cs.
/// </summary>
internal sealed class TestAuditApp : IAsyncDisposable
{
    private readonly WebApplication _app;

    private TestAuditApp(WebApplication app, Mock<IAuditServiceContract> auditService, HttpClient client)
    {
        _app = app;
        AuditService = auditService;
        Client = client;
    }

    public Mock<IAuditServiceContract> AuditService { get; }

    public HttpClient Client { get; }

    public IReadOnlyList<Endpoint> Endpoints =>
        _app.Services.GetRequiredService<EndpointDataSource>().Endpoints.ToList();

    public static async Task<TestAuditApp> StartAsync(Action<Mock<IAuditServiceContract>>? configure = null)
    {
        var auditService = new Mock<IAuditServiceContract>(MockBehavior.Loose);
        configure?.Invoke(auditService);

        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.UseTestServer();
        builder.Services.AddSingleton(auditService.Object);

        var app = builder.Build();
        app.UseMiddleware<ErrorHandlingMiddleware>();
        app.UseMiddleware<RequestLoggingMiddleware>();
        app.MapAuditEndpoints();

        await app.StartAsync();

        return new TestAuditApp(app, auditService, app.GetTestClient());
    }

    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await _app.StopAsync();
        await _app.DisposeAsync();
    }
}
