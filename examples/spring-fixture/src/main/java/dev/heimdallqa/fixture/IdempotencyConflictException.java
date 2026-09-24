package dev.heimdallqa.fixture;

/**
 * A replayed idempotency key.
 *
 * The name is not decoration: the reader maps a product exception to an axis by the
 * framework's name or, when the framework does not own the concept, by a narrow
 * pattern. `Idempotency…Conflict…Exception` is the one pattern it accepts, and a
 * fixture that called this `DuplicateRequest` would leave the `I-replay` axis to a
 * human — which is a fine outcome, just not the one being measured here.
 */
public class IdempotencyConflictException extends RuntimeException {

    public IdempotencyConflictException(String message) {
        super(message);
    }
}
