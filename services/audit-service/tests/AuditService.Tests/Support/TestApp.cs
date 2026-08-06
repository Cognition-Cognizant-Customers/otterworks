using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using OtterWorks.AuditService.Controllers;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests.Support;

/// <summary>
/// Boots the audit HTTP surface in-process on <see cref="TestServer"/>. Only the endpoints and
/// middleware under test are mapped — no AWS clients and no background services — so every case
/// is hermetic and independent of any other.
/// </summary>
public sealed class TestApp : IAsyncDisposable
{
    private readonly WebApplication _app;

    private TestApp(WebApplication app, HttpClient client)
    {
        _app = app;
        Client = client;
    }

    public HttpClient Client { get; }

    public IServiceProvider Services => _app.Services;

    public IReadOnlyList<Endpoint> Endpoints =>
        _app.Services.GetRequiredService<EndpointDataSource>().Endpoints;

    public static async Task<TestApp> StartAsync(
        Action<IServiceCollection>? configureServices = null,
        Action<WebApplication>? configureApp = null)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            // Production so the developer exception page never intercepts an unhandled
            // exception ahead of the service's own error-handling middleware.
            EnvironmentName = Environments.Production,
            ContentRootPath = AppContext.BaseDirectory,
        });

        builder.Logging.ClearProviders();
        builder.WebHost.UseTestServer();
        configureServices?.Invoke(builder.Services);

        var app = builder.Build();
        configureApp?.Invoke(app);

        await app.StartAsync();
        return new TestApp(app, app.GetTestClient());
    }

    /// <summary>
    /// The audit API exactly as <c>Program.cs</c> maps it, backed by a fake service.
    /// </summary>
    public static Task<TestApp> StartAuditApiAsync(IAuditService auditService) =>
        StartAsync(
            services => services.AddSingleton(auditService),
            app => app.MapAuditEndpoints());

    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await _app.StopAsync();
        await _app.DisposeAsync();
    }
}
