"""Base snapshots (the "main" branch) of the three sample repositories.  Every test passes on main.

calculator  - lab-manual project: calculator.py, test_calculator.py (6 tests), app.py
inventory   - package `inventory` with tests/ (16 tests)
textkit     - package `textkit` with tests/ (16 tests)
"""
from __future__ import annotations

CALCULATOR = {
    "README.md": """# calculator

A tiny calculator library with a command-line front end.

```
python app.py add 2 3
python -m pytest
```
""",
    "calculator.py": '''"""Basic arithmetic helpers used by the command-line calculator."""


def add(a, b):
    """Return a + b."""
    return a + b


def subtract(a, b):
    """Return a - b."""
    return a - b


def multiply(a, b):
    """Return a * b."""
    return a * b


def divide(a, b):
    """Return a / b; dividing by zero raises ValueError."""
    if b == 0:
        raise ValueError("cannot divide by zero")
    return a / b


def average(numbers):
    """Return the arithmetic mean of a non-empty sequence."""
    if not numbers:
        raise ValueError("average() needs at least one number")
    return sum(numbers) / len(numbers)
''',
    "app.py": '''"""Command-line front end: python app.py add 2 3"""
import sys

from calculator import add, divide, multiply, subtract

OPS = {"add": add, "subtract": subtract, "multiply": multiply, "divide": divide}


def run(op, a, b):
    """Apply one operation to two numbers."""
    return OPS[op](float(a), float(b))


def main(argv):
    """Entry point: write the result of `op a b` to stdout."""
    if len(argv) != 4 or argv[1] not in OPS:
        sys.stderr.write("usage: app.py add|subtract|multiply|divide A B\\n")
        return 2
    sys.stdout.write(f"{run(argv[1], argv[2], argv[3])}\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "test_calculator.py": '''import pytest

from calculator import add, average, divide, multiply, subtract


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_multiply():
    assert multiply(4, 3) == 12


def test_divide():
    assert divide(10, 4) == 2.5


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)


def test_average():
    assert average([2, 4, 6]) == 4
''',
}

INVENTORY = {
    "README.md": """# inventory

In-memory stock bookkeeping: a `Store` that tracks units on hand and reserved, price helpers and plain-text reports.

```
python -m pytest
```
""",
    "inventory/__init__.py": '"""Stock bookkeeping, pricing and reports."""\n',
    "inventory/config.py": '''"""Runtime configuration read from the environment."""
import os

LOW_STOCK_THRESHOLD = 5
API_TOKEN = os.environ.get("INVENTORY_API_TOKEN", "")
''',
    "inventory/pricing.py": '''"""Price calculations."""


def apply_discount(price, percent):
    """Return price reduced by percent (0-100), rounded to cents."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (1 - percent / 100), 2)


def add_tax(price, rate=0.2):
    """Return price plus tax at the given rate, rounded to cents."""
    return round(price * (1 + rate), 2)


def bulk_price(unit_price, quantity):
    """Total for quantity units; orders of 10 or more get 5 percent off."""
    total = unit_price * quantity
    if quantity >= 10:
        total = apply_discount(total, 5)
    return round(total, 2)
''',
    "inventory/store.py": '''"""In-memory stock bookkeeping."""


class Store:
    """Tracks units on hand and units reserved for open orders."""

    def __init__(self):
        self._on_hand = {}
        self._reserved = {}

    def add_item(self, sku, quantity=0):
        """Register a SKU with an opening quantity."""
        if quantity < 0:
            raise ValueError("quantity must not be negative")
        self._on_hand[sku] = quantity
        self._reserved[sku] = 0

    def stock(self, sku):
        """Units on hand that are not reserved."""
        self._require(sku)
        return self._on_hand[sku] - self._reserved[sku]

    def reserve(self, sku, quantity):
        """Reserve units for an order; fails when there is not enough free stock."""
        self._require(sku)
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if quantity > self.stock(sku):
            raise ValueError("not enough stock")
        self._reserved[sku] += quantity

    def restock(self, sku, quantity):
        """Add delivered units to a SKU."""
        self._require(sku)
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._on_hand[sku] += quantity

    def low_stock(self, threshold):
        """SKUs whose free stock is at or below threshold."""
        return sorted(sku for sku in self._on_hand if self.stock(sku) <= threshold)

    def total_units(self):
        """Units on hand across all SKUs."""
        return sum(self._on_hand.values())

    def _require(self, sku):
        if sku not in self._on_hand:
            raise KeyError(sku)
''',
    "inventory/reports.py": '''"""Plain-text stock reports."""
from .config import LOW_STOCK_THRESHOLD


def stock_report(store, skus):
    """One line per SKU with its free stock."""
    return "\\n".join(f"{sku}: {store.stock(sku)}" for sku in skus)


def low_stock_report(store):
    """List the SKUs that need restocking."""
    low = store.low_stock(LOW_STOCK_THRESHOLD)
    return "nothing to restock" if not low else "restock: " + ", ".join(low)
''',
    "tests/test_pricing.py": '''import pytest

from inventory.pricing import add_tax, apply_discount, bulk_price


def test_discount():
    assert apply_discount(100, 25) == 75


def test_discount_zero():
    assert apply_discount(80, 0) == 80


def test_discount_rejects_out_of_range():
    with pytest.raises(ValueError):
        apply_discount(10, 120)


def test_add_tax():
    assert add_tax(100, 0.2) == 120


def test_bulk_price_small_order():
    assert bulk_price(2.5, 4) == 10


def test_bulk_price_discounted():
    assert bulk_price(10, 10) == 95
''',
    "tests/test_store.py": '''import pytest

from inventory.store import Store


@pytest.fixture
def store():
    s = Store()
    s.add_item("A1", 10)
    s.add_item("B2", 3)
    return s


def test_add_item_sets_stock(store):
    assert store.stock("A1") == 10


def test_add_item_rejects_negative():
    with pytest.raises(ValueError):
        Store().add_item("X", -1)


def test_reserve_reduces_free_stock(store):
    store.reserve("A1", 4)
    assert store.stock("A1") == 6


def test_reserve_too_much(store):
    with pytest.raises(ValueError):
        store.reserve("B2", 5)


def test_reserve_rejects_non_positive(store):
    with pytest.raises(ValueError):
        store.reserve("A1", 0)


def test_restock_adds_units(store):
    store.restock("B2", 7)
    assert store.stock("B2") == 10


def test_restock_rejects_non_positive(store):
    with pytest.raises(ValueError):
        store.restock("B2", 0)


def test_unknown_sku(store):
    with pytest.raises(KeyError):
        store.stock("ZZ")


def test_low_stock(store):
    assert store.low_stock(5) == ["B2"]


def test_total_units(store):
    assert store.total_units() == 13
''',
}

TEXTKIT = {
    "README.md": """# textkit

Small text utilities: tokenizing, statistics, slugs and case conversion.

```
python -m pytest
```
""",
    "textkit/__init__.py": '"""Small text utilities."""\n',
    "textkit/tokenize.py": '''"""Split text into words and sentences."""
import re

WORD = re.compile(r"[A-Za-z0-9']+")
SENTENCE_END = re.compile(r"(?<=[.!?])\\s+")
# TODO: support non-English apostrophes


def word_tokens(text):
    """Return the words of text in order."""
    return WORD.findall(text)


def sentences(text):
    """Split text into sentences at ., ! and ?."""
    return [s for s in SENTENCE_END.split(text.strip()) if s]
''',
    "textkit/stats.py": '''"""Simple text statistics."""
from collections import Counter

from .tokenize import word_tokens


def word_count(text):
    """Number of words in text."""
    return len(word_tokens(text))


def most_common(text, n=3):
    """The n most frequent words (lower-cased) with their counts."""
    counts = Counter(w.lower() for w in word_tokens(text))
    return counts.most_common(n)
''',
    "textkit/slug.py": '''"""URL-friendly slugs."""
import re

_SEPARATORS = re.compile(r"[^a-z0-9]+")


def slugify(text, max_length=60):
    """Lower-case text, join the words with '-' and trim to max_length."""
    slug = _SEPARATORS.sub("-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-")


def is_slug(text):
    """True when text is already a valid slug."""
    return bool(text) and slugify(text, len(text)) == text
''',
    "textkit/case.py": '''"""Case conversion helpers."""
import re


def snake_case(text):
    """'Hello World' -> 'hello_world'."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def title_case(text):
    """Capitalise every word."""
    return " ".join(w.capitalize() for w in text.split())


def camel_case(text):
    """'hello big world' -> 'helloBigWorld'."""
    head, *rest = text.split()
    return head.lower() + "".join(w.capitalize() for w in rest)
''',
    "tests/test_slug.py": '''from textkit.slug import is_slug, slugify


def test_basic_slug():
    assert slugify("Hello, World!") == "hello-world"


def test_slug_trims_length():
    assert slugify("a very long title indeed", 10) == "a-very-lon"


def test_slug_strips_edges():
    assert slugify("  --Hi--  ") == "hi"


def test_is_slug():
    assert is_slug("hello-world") and not is_slug("Hello World")
''',
    "tests/test_tokenize_stats.py": '''from textkit.stats import most_common, word_count
from textkit.tokenize import sentences, word_tokens


def test_word_tokens():
    assert word_tokens("Hello, big world!") == ["Hello", "big", "world"]


def test_word_tokens_keeps_apostrophes():
    assert word_tokens("it's fine") == ["it's", "fine"]


def test_sentences():
    assert sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]


def test_word_count():
    assert word_count("a b c d") == 4


def test_most_common():
    assert most_common("a b a c a b", 2) == [("a", 3), ("b", 2)]


def test_most_common_ignores_case():
    assert most_common("Go go GO", 1) == [("go", 3)]
''',
    "tests/test_case.py": '''from textkit.case import camel_case, snake_case, title_case


def test_snake_case():
    assert snake_case("Hello World") == "hello_world"


def test_snake_case_punctuation():
    assert snake_case("  Hello, World! ") == "hello_world"


def test_title_case():
    assert title_case("hello big world") == "Hello Big World"


def test_title_case_mixed():
    assert title_case("hELLO") == "Hello"


def test_camel_case():
    assert camel_case("hello big world") == "helloBigWorld"


def test_camel_case_single_word():
    assert camel_case("Hello") == "hello"
''',
}

REPOS = {"calculator": CALCULATOR, "inventory": INVENTORY, "textkit": TEXTKIT}
