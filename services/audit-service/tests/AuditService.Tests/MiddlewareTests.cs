using System.Net;
using System.Text.Json;
using AuditService.Tests.Support;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using OtterWorks.AuditService.Middleware;

namespace AuditService.Tests;

/// <summary>
/// <see cref="ErrorHandlingMiddleware"/> and <see cref="RequestLoggingMiddleware"/>, exercised
/// through a real ASP.NET Core pipeline on <see cref="Microsoft.AspNetCore.TestHost.TestServer"/>.
/// </summary>
public class MiddlewareTests
{
    private const string CorrelationHeader = "X-Request-ID";

    private static Task<TestApp> StartAsync(
        RequestDelegate terminal,
        CapturingLogger<ErrorHandlingMiddleware> errorLogger,
        CapturingLogger<RequestLoggingMiddleware> requestLogger) =>
        TestApp.StartAsync(
            services =>
            {
                services.AddSingleton<ILogger<ErrorHandlingMiddleware>>(errorLogger);
                services.AddSingleton<ILogger<RequestLoggingMiddleware>>(requestLogger);
            },
            app =>
            {
                // Same order as Program.cs.
                app.UseMiddleware<ErrorHandlingMiddleware>();
                app.UseMiddleware<RequestLoggingMiddleware>();
                app.Run(terminal);
            });

    // -------------------------------------------------- ErrorHandlingMiddleware

    [Fact]
    public async Task SuccessfulRequest_PassesThroughUntouched()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            ctx => ctx.Response.WriteAsync("ok"), errorLogger, requestLogger);

        var response = await app.Client.GetAsync("/anything");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("ok", await response.Content.ReadAsStringAsync());
        Assert.False(errorLogger.HasLevel(LogLevel.Error));
    }

    [Fact]
    public async Task UnhandledException_IsConvertedToA500JsonProblem()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            _ => throw new InvalidOperationException("boom"), errorLogger, requestLogger);

        var response = await app.Client.GetAsync("/api/v1/audit/events");

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.Equal("application/json", response.Content.Headers.ContentType?.MediaType);

        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.Equal("An internal server error occurred.", body.RootElement.GetProperty("error").GetString());
        Assert.False(string.IsNullOrWhiteSpace(body.RootElement.GetProperty("traceId").GetString()));
    }

    [Fact]
    public async Task UnhandledException_DoesNotLeakTheExceptionDetailToTheCaller()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            _ => throw new InvalidOperationException("connection string user=admin;password=hunter2"),
            errorLogger,
            requestLogger);

        var response = await app.Client.GetAsync("/api/v1/audit/events");
        var payload = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain("hunter2", payload, StringComparison.Ordinal);
        Assert.DoesNotContain("InvalidOperationException", payload, StringComparison.Ordinal);
    }

    [Fact]
    public async Task UnhandledException_IsLoggedWithTheOriginalException()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        var failure = new InvalidOperationException("boom");
        await using var app = await StartAsync(_ => throw failure, errorLogger, requestLogger);

        await app.Client.GetAsync("/api/v1/audit/events");

        var entry = Assert.Single(errorLogger.Entries.Where(e => e.Level == LogLevel.Error));
        Assert.Same(failure, entry.Exception);
        Assert.Contains("/api/v1/audit/events", entry.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ExceptionAfterResponseHasStarted_IsRethrownRatherThanRewritten()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            async ctx =>
            {
                await ctx.Response.WriteAsync("partial");
                await ctx.Response.Body.FlushAsync();
                throw new InvalidOperationException("late failure");
            },
            errorLogger,
            requestLogger);

        var thrown = await Record.ExceptionAsync(() => app.Client.GetStringAsync("/late"));

        Assert.NotNull(thrown);
        Assert.True(errorLogger.HasLevel(LogLevel.Warning));
        Assert.DoesNotContain(
            errorLogger.Entries,
            e => e.Message.Contains("An internal server error occurred.", StringComparison.Ordinal));
    }

    [Fact]
    public async Task NonSuccessStatusFromTheEndpoint_IsNotRewritten()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            ctx =>
            {
                ctx.Response.StatusCode = StatusCodes.Status404NotFound;
                return Task.CompletedTask;
            },
            errorLogger,
            requestLogger);

        var response = await app.Client.GetAsync("/missing");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.False(errorLogger.HasLevel(LogLevel.Error));
    }

    // ------------------------------------------------ RequestLoggingMiddleware

    [Fact]
    public async Task RequestLogging_RecordsMethodPathAndStatusOnSuccess()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            ctx =>
            {
                ctx.Response.StatusCode = StatusCodes.Status201Created;
                return Task.CompletedTask;
            },
            errorLogger,
            requestLogger);

        await app.Client.PostAsync("/api/v1/audit/events", new StringContent("{}"));

        var entry = Assert.Single(requestLogger.Entries.Where(e => e.Level == LogLevel.Information));
        Assert.Contains("POST", entry.Message, StringComparison.Ordinal);
        Assert.Contains("/api/v1/audit/events", entry.Message, StringComparison.Ordinal);
        Assert.Contains("201", entry.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task RequestLogging_LogsAndRethrowsOnFailure()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        var failure = new InvalidOperationException("boom");
        await using var app = await StartAsync(_ => throw failure, errorLogger, requestLogger);

        var response = await app.Client.GetAsync("/api/v1/audit/export");

        // Rethrown by RequestLoggingMiddleware and converted by ErrorHandlingMiddleware outside it.
        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);

        var entry = Assert.Single(requestLogger.Entries.Where(e => e.Level == LogLevel.Error));
        Assert.Same(failure, entry.Exception);
        Assert.DoesNotContain(requestLogger.Entries, e => e.Level == LogLevel.Information);
    }

    [Fact]
    public async Task RequestLogging_BehavesIdenticallyWithAndWithoutTheCorrelationHeader()
    {
        var withHeaderLogger = new CapturingLogger<RequestLoggingMiddleware>();
        var withoutHeaderLogger = new CapturingLogger<RequestLoggingMiddleware>();

        await using (var app = await StartAsync(
                         ctx => Task.CompletedTask,
                         new CapturingLogger<ErrorHandlingMiddleware>(),
                         withHeaderLogger))
        {
            var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/events");
            request.Headers.Add(CorrelationHeader, "corr-123");
            await app.Client.SendAsync(request);
        }

        await using (var app = await StartAsync(
                         ctx => Task.CompletedTask,
                         new CapturingLogger<ErrorHandlingMiddleware>(),
                         withoutHeaderLogger))
        {
            await app.Client.GetAsync("/api/v1/audit/events");
        }

        var withHeader = Assert.Single(withHeaderLogger.Entries);
        var withoutHeader = Assert.Single(withoutHeaderLogger.Entries);
        Assert.Equal(withoutHeader.Level, withHeader.Level);
        // FINDING (audit-4): the inbound correlation id is neither logged nor propagated, so an
        // audit record cannot be tied back to the gateway request that produced it.
        Assert.DoesNotContain("corr-123", withHeader.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task IncomingCorrelationId_IsNotEchoedOnASuccessfulResponse()
    {
        await using var app = await StartAsync(
            ctx => Task.CompletedTask,
            new CapturingLogger<ErrorHandlingMiddleware>(),
            new CapturingLogger<RequestLoggingMiddleware>());

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/events");
        request.Headers.Add(CorrelationHeader, "corr-123");
        var response = await app.Client.SendAsync(request);

        Assert.False(response.Headers.Contains(CorrelationHeader));
    }

    [Fact]
    public async Task ErrorResponse_UsesTheFrameworkTraceIdNotTheInboundCorrelationId()
    {
        await using var app = await StartAsync(
            _ => throw new InvalidOperationException("boom"),
            new CapturingLogger<ErrorHandlingMiddleware>(),
            new CapturingLogger<RequestLoggingMiddleware>());

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/events");
        request.Headers.Add(CorrelationHeader, "corr-123");
        var response = await app.Client.SendAsync(request);

        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.NotEqual("corr-123", body.RootElement.GetProperty("traceId").GetString());
    }

    [Fact(Skip = "FINDING (audit-4): neither middleware reads X-Request-ID. The gateway generates and " +
                 "forwards one (services/api-gateway/internal/middleware/requestid.go), but audit-service " +
                 "drops it: it is absent from the request log, from the error payload's traceId and from " +
                 "the response headers, so audit entries cannot be correlated with the originating request. " +
                 "Defect fix is a separate PR; do not fix in a coverage PR.")]
    public async Task IncomingCorrelationId_ShouldBePropagatedToLogsAndResponses()
    {
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            _ => throw new InvalidOperationException("boom"),
            new CapturingLogger<ErrorHandlingMiddleware>(),
            requestLogger);

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/events");
        request.Headers.Add(CorrelationHeader, "corr-123");
        var response = await app.Client.SendAsync(request);

        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.Equal("corr-123", body.RootElement.GetProperty("traceId").GetString());
        Assert.Contains(requestLogger.Entries, e => e.Message.Contains("corr-123", StringComparison.Ordinal));
        Assert.Equal("corr-123", Assert.Single(response.Headers.GetValues(CorrelationHeader)));
    }

    [Fact]
    public async Task PipelineOrder_LogsTheFailureAndStillReturnsTheJsonErrorBody()
    {
        var errorLogger = new CapturingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new CapturingLogger<RequestLoggingMiddleware>();
        await using var app = await StartAsync(
            _ => throw new InvalidOperationException("boom"), errorLogger, requestLogger);

        var response = await app.Client.GetAsync("/api/v1/audit/reports/compliance");

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.True(requestLogger.HasLevel(LogLevel.Error));
        Assert.True(errorLogger.HasLevel(LogLevel.Error));
    }
}
