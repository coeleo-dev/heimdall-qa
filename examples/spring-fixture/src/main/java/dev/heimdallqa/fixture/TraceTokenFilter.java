package dev.heimdallqa.fixture;

import java.io.IOException;
import java.util.UUID;

import tools.jackson.databind.ObjectMapper;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * The instrumentation a log file and an auth header need, in one filter.
 *
 * It does two jobs because the fixture is meant to be small, and both jobs are the
 * ones a real API does somewhere else:
 *
 * **The trace.** The harness stamps `X-Trace-Id` into the request and expects it back
 * in the response and in a log line. Micrometer does this in a real application; here
 * a `MDC.put` and a logback pattern do it, and the line is written *before* the
 * handler runs, which is what makes the file readable right after the response.
 *
 * **The credential.** A missing or wrong `X-Fixture-Token` is refused here and not in
 * a handler, which is deliberate: Spring Security refuses at a filter, so the reader
 * cannot see the 401 in an `@ExceptionHandler` table — that is exactly why `auth` is
 * the one axis `DISCOVERY.md` asks a human to declare. The status is still real,
 * which is what the negative case checks.
 *
 * Every route requires the token, including the two `GET`s. A public read route would
 * be the more realistic fixture and the less useful one: `coverage.expand` derives an
 * `N-auth` case for every authenticated route, so a `GET` that answers 200 without a
 * credential would generate a case asserting a 401 the API does not send.
 */
@Component
public class TraceTokenFilter extends OncePerRequestFilter {

    public static final String TRACE_MDC_KEY = "traceId";

    private static final Logger log = LoggerFactory.getLogger(TraceTokenFilter.class);
    private static final String TRACE_HEADER = "X-Trace-Id";
    private static final String TOKEN_HEADER = "X-Fixture-Token";
    private static final String TOKEN = "fixture-token";

    private final ObjectMapper mapper;

    public TraceTokenFilter(ObjectMapper mapper) {
        this.mapper = mapper;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain chain) throws ServletException, IOException {
        String trace = request.getHeader(TRACE_HEADER);
        if (trace == null || trace.isBlank()) {
            trace = UUID.randomUUID().toString();
        }
        MDC.put(TRACE_MDC_KEY, trace);
        response.setHeader(TRACE_HEADER, trace);
        try {
            if (!TOKEN.equals(request.getHeader(TOKEN_HEADER))) {
                log.info("refused {} {} without a token", request.getMethod(), request.getRequestURI());
                refuse(response, trace);
                return;
            }
            log.info("handled {} {}", request.getMethod(), request.getRequestURI());
            chain.doFilter(request, response);
        } finally {
            MDC.remove(TRACE_MDC_KEY);
        }
    }

    private void refuse(HttpServletResponse response, String trace) throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json");
        response.getWriter().write(
                mapper.writeValueAsString(ApiErrorBody.of("a fixture token is required")));
    }
}
