package dev.heimdallqa.fixture;

import org.slf4j.MDC;

/**
 * The body every failure answers with: `error` and `traceId`, which is what the
 * harness calls the `spring` envelope.
 *
 * `of` reads the trace from the MDC rather than taking it as a parameter, because
 * that is how a real advice builds it — and it is why the reader believes the
 * envelope: the construction names the shape, the return type only names the
 * wrapper.
 */
public record ApiErrorBody(String error, String traceId) {

    public static ApiErrorBody of(String error) {
        String trace = MDC.get(TraceTokenFilter.TRACE_MDC_KEY);
        return new ApiErrorBody(error, trace == null ? "" : trace);
    }
}
