package dev.heimdallqa.fixture;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/**
 * The four annotations the reader has to understand, on purpose, in one record.
 *
 * `@NotBlank`/`@NotNull` are the `required` attribute, `@Size(max = …)` is
 * `max_length`, `@Pattern` is the pattern an `N-pattern` case exercises, and
 * `@JsonProperty` is the wire name. A fixture that covered three of the four would
 * leave the fourth measured by nothing.
 */
public record ItemRequest(
        @NotBlank @Size(max = 40) String name,
        @NotNull @Size(min = 2, max = 8) @JsonProperty("item_label") String label,
        @NotNull @Pattern(regexp = "^[A-Z]{3}$") @JsonProperty("item_code") String code) {
}
