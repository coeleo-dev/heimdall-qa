package dev.heimdallqa.fixture;

import java.util.NoSuchElementException;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * The failure table, written once and read by the reader instead of by a human.
 *
 * Every method names exactly one status, because a reader that has to choose between
 * two would have to guess; the two shortcuts (`badRequest`, `status(CONFLICT)`) are
 * both here on purpose, since a table written only one way would not prove the
 * reader handles the other. The catch-all answers 500 and is never mapped to an axis
 * — a 5xx is infrastructure, not a case family.
 *
 * `MethodArgumentTypeMismatchException` is deliberately not one of the framework
 * names the reader maps: it answers 400 and a human still has to author the rule.
 * The report names it, which is the honest outcome for a failure no case family
 * describes.
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiErrorBody> invalidBody(MethodArgumentNotValidException ex) {
        String detail = ex.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getField() + " " + error.getDefaultMessage())
                .sorted()
                .findFirst()
                .orElse("the body is not valid");
        return ResponseEntity.badRequest().body(ApiErrorBody.of(detail));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiErrorBody> unreadableBody(HttpMessageNotReadableException ex) {
        return ResponseEntity.badRequest().body(ApiErrorBody.of("the body could not be read"));
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<ApiErrorBody> missingHeader(MissingRequestHeaderException ex) {
        return ResponseEntity.badRequest()
                .body(ApiErrorBody.of(ex.getHeaderName() + " is required"));
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ApiErrorBody> wrongType(MethodArgumentTypeMismatchException ex) {
        return ResponseEntity.badRequest()
                .body(ApiErrorBody.of(ex.getName() + " is not a valid value"));
    }

    @ExceptionHandler(IdempotencyConflictException.class)
    public ResponseEntity<ApiErrorBody> conflict(IdempotencyConflictException ex) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(ApiErrorBody.of(ex.getMessage()));
    }

    @ExceptionHandler(NoSuchElementException.class)
    public ResponseEntity<ApiErrorBody> notFound(NoSuchElementException ex) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ApiErrorBody.of(ex.getMessage()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiErrorBody> unexpected(Exception ex) {
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiErrorBody.of("the fixture failed unexpectedly"));
    }
}
