"""The Java scanner, on the constructs the reference corpus is made of.

Every case here is a shape a real file in the corpus has: a record whose components
carry five annotations each, a class whose body holds a nested `switch`, a text block
with braces in it, a `@RequestMapping` that names its method instead of implying one.
The scanner is not a parser and does not try to be, so the tests are about the two
things it must get right — find the declaration, and never guess at what it says.
"""

from heimdall_qa_spring.java import parse_java
from heimdall_qa_spring.java import parse_parameter
from heimdall_qa_spring.java import split_arguments
from heimdall_qa_spring.java import strip_comments
from heimdall_qa_spring.java import type_head
from heimdall_qa_spring.java import unquote


def test_a_record_declares_its_components_with_their_annotations():
    source = """
        package com.example.dto;

        public record IngestRequest(
                @NotBlank
                @Size(max = 128)
                @JsonProperty("transaction_id")
                String transactionId,
                @NotNull Map<String, Object> properties) {
        }
    """

    declared = parse_java("IngestRequest.java", source).types[0]

    assert declared.kind == "record"
    assert declared.name == "IngestRequest"
    assert declared.fqn == "com.example.dto.IngestRequest"
    assert [component.name for component in declared.components] == [
        "transactionId",
        "properties",
    ]
    assert declared.components[0].names() == ("NotBlank", "Size", "JsonProperty")
    assert declared.components[0].annotation("Size").named_int("max") == 128
    assert declared.components[0].annotation("JsonProperty").value() == "transaction_id"


def test_a_generic_component_type_is_kept_whole_and_its_head_is_the_erasure():
    component = parse_parameter("Map<String, List<Object>> properties")

    assert component.type == "Map<String, List<Object>>"
    assert type_head(component.type) == "Map"


def test_arguments_are_split_on_top_level_commas_only():
    assert split_arguments('max = 8, regexp = "^a,b$"') == ["max = 8", 'regexp = "^a,b$"']
    # A `{...}` array is one argument, so its commas are not the separators.
    assert split_arguments("{A.class, B.class}") == ["{A.class, B.class}"]


def test_a_string_literal_hides_its_braces_from_the_scanner():
    source = """
        class C {
            void m() {
                String json = "{\\"a\\": 1}";
                int x = 1;
            }
        }
    """

    declared = parse_java("C.java", source).types[0]

    assert [method.name for method in declared.methods()] == ["m"]


def test_a_text_block_hides_its_braces_too():
    source = '''
        class C {
            String body = """
                { "open": "}" }
                """;
            void m() { }
        }
    '''

    declared = parse_java("C.java", source).types[0]

    assert [method.name for method in declared.methods()] == ["m"]


def test_comments_are_blanked_and_a_double_slash_inside_a_string_survives():
    text = 'String url = "http://x"; // trailing\nint n = 1;'
    stripped = strip_comments(text)

    assert '"http://x"' in stripped
    assert "trailing" not in stripped
    assert len(stripped) == len(text)


def test_a_nested_class_becomes_a_type_of_its_own_with_a_dotted_name():
    source = """
        package com.example;
        class Outer {
            static class Inner {
                void m() { }
            }
        }
    """

    names = [declared.fqn for declared in parse_java("Outer.java", source).all_types()]

    assert names == ["com.example.Outer", "com.example.Outer.Inner"]


def test_a_switch_inside_a_method_body_does_not_end_the_method_early():
    source = """
        class C {
            int m(int x) {
                switch (x) {
                    case 1: return 1;
                    default: return 2;
                }
            }
            int after() { return 3; }
        }
    """

    declared = parse_java("C.java", source).types[0]

    assert [method.name for method in declared.methods()] == ["m", "after"]
    body = next(method.body for method in declared.methods() if method.name == "m")
    assert "case 1" in body


def test_a_field_keeps_its_initializer_which_is_where_a_constant_holds_its_values():
    source = """
        class C {
            private static final Set<String> DENIED_KEYS = Set.of("cpf", "cnpj");
            void m() { }
        }
    """

    declared = parse_java("C.java", source).types[0]

    assert declared.fields()[0].name == "DENIED_KEYS"
    assert unquote('"cpf"') in declared.fields()[0].body


def test_a_compact_constructor_is_kept_so_its_throws_can_be_read():
    source = """
        record ApiErrorBody(String error, String traceId) {
            public ApiErrorBody {
                Objects.requireNonNull(error, "error must not be null");
            }
        }
    """

    declared = parse_java("ApiErrorBody.java", source).types[0]
    compact = [method for method in declared.methods() if method.is_constructor]

    assert [method.name for method in compact] == ["ApiErrorBody"]
    assert "requireNonNull" in compact[0].body


def test_an_unreadable_construct_yields_nothing_rather_than_a_guess():
    assert parse_java("C.java", "class C { ??? }").types[0].methods() == ()


def test_unquote_resolves_the_escapes_java_would():
    assert unquote(r'"\\d{4}-\\d{2}-\\d{2}"') == r"\d{4}-\d{2}-\d{2}"
    assert unquote('"a\\nb"') == "a\nb"
    assert unquote('"plain"') == "plain"
