"""One pattern for every "Add …" button (D-196, #110) and the select chevron (D-197).

Every add button says "Add {thing}" and starts with the plus icon. A page's main add
button sits in the page header; a card's sits on the right of its heading row.
"""

import re
from pathlib import Path

import pytest

from app.services import learning_service as learning

TEMPLATES = Path("app/templates")
CSS = Path("app/static/css/input.css")
PAGE_CLASSES = "h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-[#4F46E5]"


def header_of(html: str) -> str:
    """The page header bar (components/page_header.html), the first <header> in <main>."""
    main = html[html.index("<main") :]
    return main[main.index("<header") : main.index("</header>")]


@pytest.fixture
def goal(session, user):
    return learning.create_goal(
        session,
        user,
        title="AWS",
        target_date=None,
        weekly_hours=None,
        resource=None,
        plan_weekly=False,
    )


@pytest.mark.parametrize(
    ("url", "label"),
    [
        ("/", "Add job"),
        ("/learning", "Add goal"),
        ("/documents", "Add CV"),
        ("/documents?tab=letters", "Add cover letter"),
    ],
)
def test_page_add_button_is_in_the_header_with_a_plus(auth_client, url, label):
    header = header_of(auth_client.get(url).text)

    assert PAGE_CLASSES in header
    assert re.search(rf"</svg>{label}</button>", header)


def test_documents_tab_row_has_no_add_button_any_more(auth_client):
    page = auth_client.get("/documents").text
    body = page[page.index("</header>", page.index("<main")) :]

    assert "bg-[#4F46E5] px-3" not in body  # the old "New CV" button in the tab row


def test_learning_card_buttons_sit_right_of_the_card_heading(auth_client, goal):
    page = auth_client.get("/learning").text

    plan = page[page.index('id="plan-title"') :]
    assert plan.index("</h2>") < plan.index("</svg>Add task</button>") < plan.index("<select")
    goals = page[page.index('id="goals-title"') :]
    assert "</svg>Add goal</button>" in goals[: goals.index("<article")]


def test_goal_row_add_task_names_the_goal_with_the_visible_words(auth_client, goal):
    page = auth_client.get("/learning").text

    # The screen reader name starts with the visible text (WCAG 2.5.3).
    assert 'aria-label="Add task to AWS"' in page
    assert "Add a task to" not in page


def test_modal_titles_and_submit_buttons_use_add(auth_client):
    learning_page = auth_client.get("/learning").text
    documents = auth_client.get("/documents").text
    letters = auth_client.get("/documents?tab=letters").text

    assert ">Add goal</h2>" in learning_page or "Add goal</h2>" in learning_page
    assert "Add CV</h2>" in documents and "Add cover letter</h2>" in letters
    for page in (learning_page, documents, letters):
        assert not re.search(r">\s*(New goal|New CV|New cover letter)\s*<", page)
        assert not re.search(r">\s*Create (goal|CV|cover letter)\s*<", page)


# Button texts that may still start with New/Create: they don't add an item to a list.
ALLOWED = {"Create account"}


def template_code(path: Path) -> str:
    """A template without its {# … #} comments (they hold usage examples)."""
    return re.sub(r"\{#.*?#\}", "", path.read_text(), flags=re.S)


def button_texts():
    """The visible text of every <button> and primary_button(...) in the templates."""
    for path in TEMPLATES.rglob("*.html"):
        text = template_code(path)
        for match in re.finditer(r"<button\b[^>]*>(.*?)</button>", text, re.S):
            yield path, re.sub(r"\{\{.*?\}\}|<[^>]+>", "", match.group(1)).strip()
        for match in re.finditer(r'primary_button\("([^"]+)"', text):
            yield path, match.group(1)


def test_no_button_says_new_or_create_or_plus_text():
    wrong = [
        (str(path), label)
        for path, label in button_texts()
        if label not in ALLOWED and re.match(r"(New|Create|\+)\b|\+ ", label)
    ]

    assert wrong == []


def test_every_add_button_shows_the_plus_icon():
    missing = []
    for path in TEMPLATES.rglob("*.html"):
        for match in re.finditer(r"<button\b[^>]*>(.*?)</button>", template_code(path), re.S):
            inner = match.group(1)
            label = re.sub(r"\{\{.*?\}\}|<[^>]+>", "", inner).strip()
            if label.startswith("Add ") and 'icon("plus")' not in inner:
                missing.append((str(path), label))

    assert missing == []


def test_selects_draw_their_own_chevron_with_room_on_the_right():
    css = CSS.read_text()
    rule = css[css.index("  select {\n    appearance: none;") :]
    rule = rule[: rule.index("}")]

    assert "padding-right: 36px" in rule
    assert "background-position: right 12px center" in rule
    assert 'url("../img/select-chevron.svg")' in rule  # a file: the CSP blocks data: images
    assert Path("app/static/img/select-chevron.svg").is_file()
    assert "forced-colors: active" in css  # high contrast gets the browser's arrow back
