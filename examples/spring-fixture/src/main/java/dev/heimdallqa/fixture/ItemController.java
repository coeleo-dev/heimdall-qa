package dev.heimdallqa.fixture;

import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

import jakarta.validation.Valid;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Three routes, one per shape the harness has to derive.
 *
 * `GET /health` is the one with no body. `POST /items` is the one with a validated
 * record and an idempotency key, so every `N-*`, `B-*` and `I-*` family has
 * something to ask about. `GET /items/{id}` is the one with a path parameter, which
 * is how the templating and the `N-notfound` family get exercised.
 *
 * The store is in memory and one item is seeded at id 1, so a case that reads item 1
 * answers the same thing on a freshly started process as on the hundredth request.
 *
 * **The id is a `String`, and that is a measurement, not a preference.** The
 * harness's `N-notfound` replaces the declared path value with an opaque ghost
 * (`ghost_<hex>`) and expects the not-found status. A route typed `long` answers
 * `MethodArgumentTypeMismatchException` — 400 — for that request, so the case would
 * have failed against a correct API, and the honest fix is the fixture's: the
 * harness cannot know what a ghost has to look like, and a project that declares a
 * numeric id has no way to say so. Recorded here because the parity target is where
 * that gap is visible; the descriptor may grow a knob for it later.
 *
 * **The key store is idempotent, not merely guarded.** The harness's `I-replay` sends
 * the same key *and the same body* and expects the status the first call answered; a
 * fixture that answered 409 for any repeated key would make that case fail and would
 * also be wrong about what idempotency means. The conflict belongs to the second half
 * of the contract: the same key with a *different* body is what 409 answers.
 */
@RestController
@RequestMapping("/fixture")
public class ItemController {

    private static final long SEEDED_ID = 1L;

    private final Map<String, Map<String, Object>> keyed = new ConcurrentHashMap<>();
    private final List<Map<String, Object>> items = new CopyOnWriteArrayList<>();

    public ItemController() {
        items.add(Map.of("id", SEEDED_ID, "name", "seeded", "label", "SEED"));
    }

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "UP");
    }

    @PostMapping("/items")
    public ResponseEntity<Map<String, Object>> create(
            @Valid @RequestBody ItemRequest request,
            @RequestHeader("X-Idempotency-Key") UUID idempotencyKey) {
        Map<String, Object> previous = keyed.get(idempotencyKey.toString());
        if (previous != null) {
            if (!previous.get("name").equals(request.name())) {
                throw new IdempotencyConflictException("this key already answered a different body");
            }
            return ResponseEntity.status(HttpStatus.CREATED).body(previous);
        }
        Map<String, Object> created = Map.of(
                "id", (long) items.size() + 1,
                "name", request.name(),
                "label", request.label());
        keyed.put(idempotencyKey.toString(), created);
        items.add(created);
        return ResponseEntity.status(HttpStatus.CREATED).body(created);
    }

    @GetMapping("/items/{id}")
    public ResponseEntity<Map<String, Object>> one(@PathVariable("id") String id) {
        Map<String, Object> found = items.stream()
                .filter(item -> String.valueOf(item.get("id")).equals(id))
                .findFirst()
                .orElseThrow(() -> new NoSuchElementException("no item " + id));
        return ResponseEntity.ok(found);
    }
}
