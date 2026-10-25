// Her text cards (card.js), shared by the avatar page and the phone page: parsing and capping a card, when it hides,
// its lines and links, and the DOM it builds against a fake document. The card is model output, so the tests also hold
// that it is only ever put on a page as text, never parsed as HTML.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import {
  MAX_BODY_CHARS,
  MAX_BODY_LINES,
  MAX_LINK_CHARS,
  MAX_TITLE_CHARS,
  OPEN_LINK_URL,
  buildCard,
  cardHideMs,
  cardLines,
  isOpenableLink,
  linkPieces,
  linkRequest,
  parseCard,
} from "../../src/waifu/avatar/web/phone/card.js";

const WEB = new URL("../../src/waifu/avatar/web/", import.meta.url);

// --- A fake document: elements and text nodes, and no way to parse HTML at all. --------------------------------------

class FakeText {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = text;
  }
}

class FakeElement {
  constructor(tagName) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.className = "";
    this.children = [];
    this.listeners = new Map();
    this.href = "";
    this.rel = "";
    this.target = "";
    this.ownText = "";
  }

  set textContent(text) {
    this.children = [];
    this.ownText = String(text);
  }

  get textContent() {
    return this.ownText + this.children.map((child) => child.textContent).join("");
  }

  set innerHTML(_html) {
    throw new Error("card text must never be parsed as HTML");
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  click() {
    let prevented = false;
    this.listeners.get("click")?.({ type: "click", preventDefault: () => (prevented = true) });
    return prevented;
  }
}

const fakeDocument = {
  createElement: (tagName) => new FakeElement(tagName),
  createTextNode: (text) => new FakeText(text),
};

const elements = (node) => node.children.filter((child) => child.nodeType === 1);
const tags = (node) => elements(node).map((child) => child.tagName);
function* walk(node) {
  yield node;
  for (const child of node.children ?? []) yield* walk(child);
}

// --- parseCard --------------------------------------------------------------------------------------------------------

describe("parseCard", () => {
  it("keeps a card's title and body", () => {
    assert.deepEqual(parseCard({ title: "Cuaca 7 hari", body: "- Senin cerah\n- Rabu hujan" }), {
      title: "Cuaca 7 hari",
      body: "- Senin cerah\n- Rabu hujan",
    });
  });

  it("keeps an untitled card", () => {
    assert.deepEqual(parseCard({ title: "", body: "- satu" }), { title: "", body: "- satu" });
  });

  for (const [name, data] of [
    ["null", null],
    ["a string", "kartu"],
    ["a list", [{ title: "a", body: "b" }]],
    ["no title", { body: "- satu" }],
    ["no body", { title: "Judul" }],
    ["a body that is not text", { title: "Judul", body: ["- satu"] }],
    ["an empty body", { title: "Judul", body: "  \n " }],
  ]) {
    it(`gives no card for ${name}`, () => {
      assert.equal(parseCard(data), null);
    });
  }

  it("cuts a long title with an ellipsis", () => {
    const card = parseCard({ title: "Judul ".repeat(30), body: "- satu" });

    assert.ok(card.title.length <= MAX_TITLE_CHARS);
    assert.ok(card.title.endsWith("…"));
  });

  it("keeps at most sixty lines, the last an ellipsis", () => {
    const lines = Array.from({ length: 80 }, (_, i) => `- baris ${i + 1}`);

    const kept = parseCard({ title: "", body: lines.join("\n") }).body.split("\n");

    assert.equal(kept.length, MAX_BODY_LINES);
    assert.deepEqual(kept.slice(0, -1), lines.slice(0, MAX_BODY_LINES - 1));
    assert.equal(kept.at(-1), "…");
  });

  it("cuts a long body with an ellipsis", () => {
    const body = parseCard({ title: "", body: "kata ".repeat(1200) }).body;

    assert.ok(body.length <= MAX_BODY_CHARS);
    assert.ok(body.endsWith("…"));
  });
});

// --- cardHideMs ---------------------------------------------------------------------------------------------------------

describe("cardHideMs", () => {
  it("hides a card after its show_s", () => {
    assert.equal(cardHideMs({ show_s: 120 }), 120_000);
  });

  it("counts the time a replayed card has already shown", () => {
    assert.equal(cardHideMs({ show_s: 120, elapsed: 30.5 }), 89_500);
  });

  it("hides a replayed card whose time has passed at once", () => {
    assert.equal(cardHideMs({ show_s: 120, elapsed: 200 }), 0);
  });

  for (const event of [{ show_s: 0 }, {}, { show_s: -5 }, { show_s: "120" }, { show_s: Number.NaN }]) {
    it(`keeps a card until it is closed for ${JSON.stringify(event)}`, () => {
      assert.equal(cardHideMs(event), null);
    });
  }
});

// --- links ------------------------------------------------------------------------------------------------------------

describe("linkPieces", () => {
  it("splits text around an https link and leaves the full stop after it", () => {
    assert.deepEqual(linkPieces("Buka https://example.com/docs?a=1&b=2."), [
      { text: "Buka " },
      { text: "https://example.com/docs?a=1&b=2", url: "https://example.com/docs?a=1&b=2" },
      { text: "." },
    ]);
  });

  it("links http too, in any case", () => {
    assert.deepEqual(linkPieces("HTTP://Example.com/a"), [
      { text: "HTTP://Example.com/a", url: "HTTP://Example.com/a" },
    ]);
  });

  it("keeps a closing bracket that belongs to the link and leaves one that does not", () => {
    assert.deepEqual(linkPieces("(lihat https://en.wikipedia.org/wiki/Foo_(bar))"), [
      { text: "(lihat " },
      { text: "https://en.wikipedia.org/wiki/Foo_(bar)", url: "https://en.wikipedia.org/wiki/Foo_(bar)" },
      { text: ")" },
    ]);
  });

  it("ends a link at Japanese punctuation but keeps Japanese in its path", () => {
    assert.deepEqual(linkPieces("https://ja.wikipedia.org/wiki/東京、です"), [
      { text: "https://ja.wikipedia.org/wiki/東京", url: "https://ja.wikipedia.org/wiki/東京" },
      { text: "、です" },
    ]);
  });

  it("ends a link at a quote, so nothing can follow it into an attribute", () => {
    assert.deepEqual(linkPieces('https://example.com"onclick=alert(1)'), [
      { text: "https://example.com", url: "https://example.com" },
      { text: '"onclick=alert(1)' },
    ]);
  });

  for (const text of [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///C:/Windows/System32/calc.exe",
    "https://",
    "https://user@evil.example/",
    "https://example.com:8080/",
    `https://example.com/${"a".repeat(MAX_LINK_CHARS)}`,
  ]) {
    it(`leaves ${text.slice(0, 40)} as text`, () => {
      assert.deepEqual(linkPieces(text), [{ text }]);
    });
  }

  it("gives text without links as one piece, and nothing for an empty line", () => {
    assert.deepEqual(linkPieces("Senin cerah"), [{ text: "Senin cerah" }]);
    assert.deepEqual(linkPieces(""), []);
  });
});

describe("isOpenableLink", () => {
  it("takes only plain http and https links", () => {
    assert.equal(isOpenableLink("https://example.com/a"), true);
    assert.equal(isOpenableLink("http://example.com"), true);
    assert.equal(isOpenableLink("javascript:alert(1)"), false);
    assert.equal(isOpenableLink("https://example.com@evil.example/"), false);
  });
});

describe("linkRequest", () => {
  it("posts the clicked link with the page's token to /open-link", () => {
    const [url, init] = linkRequest("secret", "https://example.com/a");

    assert.equal(url, OPEN_LINK_URL);
    assert.equal(OPEN_LINK_URL, "/open-link");
    assert.equal(init.method, "POST");
    assert.deepEqual(init.headers, { "Content-Type": "application/json" });
    assert.deepEqual(JSON.parse(init.body), { token: "secret", url: "https://example.com/a" });
  });
});

// --- lines ------------------------------------------------------------------------------------------------------------

describe("cardLines", () => {
  it("tells bullets, text, and blank lines apart", () => {
    const lines = cardLines("- Senin\n* Selasa\n• Rabu\nCatatan: -5 derajat\n\n  - masuk");

    assert.deepEqual(
      lines.map(({ kind, pieces }) => [kind, pieces.map((piece) => piece.text).join("")]),
      [
        ["bullet", "Senin"],
        ["bullet", "Selasa"],
        ["bullet", "Rabu"],
        ["text", "Catatan: -5 derajat"],
        ["blank", ""],
        ["bullet", "masuk"],
      ],
    );
  });

  it("finds the links in each line", () => {
    const [line] = cardLines("- Docs: https://example.com/docs");

    assert.deepEqual(line.pieces, [{ text: "Docs: " }, { text: "https://example.com/docs", url: "https://example.com/docs" }]);
  });
});

// --- buildCard --------------------------------------------------------------------------------------------------------

describe("buildCard", () => {
  const card = { title: "Cuaca", body: "Minggu ini:\n- Senin cerah\n- Rabu hujan\n\n- Kamis hujan\nSumber: https://bmkg.go.id/cuaca" };

  it("builds the title, its text lines, and its runs of bullets", () => {
    const root = buildCard(fakeDocument, card);

    assert.equal(root.tagName, "DIV");
    assert.equal(root.className, "card");
    assert.deepEqual(tags(root), ["H2", "P", "UL", "UL", "P"]);
    const [title, first, list, second, source] = elements(root);
    assert.equal(title.className, "card-title");
    assert.equal(title.textContent, "Cuaca");
    assert.equal(first.className, "card-line");
    assert.equal(first.textContent, "Minggu ini:");
    assert.equal(list.className, "card-list");
    assert.deepEqual(
      elements(list).map((item) => [item.tagName, item.textContent]),
      [
        ["LI", "Senin cerah"],
        ["LI", "Rabu hujan"],
      ],
    );
    assert.deepEqual(elements(second).map((item) => item.textContent), ["Kamis hujan"]);
    assert.equal(source.textContent, "Sumber: https://bmkg.go.id/cuaca");
  });

  it("leaves out the title of an untitled card", () => {
    assert.deepEqual(tags(buildCard(fakeDocument, { title: "", body: "- satu" })), ["UL"]);
  });

  it("makes a link that opens apart from the page", () => {
    const [, , , , source] = elements(buildCard(fakeDocument, card));

    const [link] = elements(source);
    assert.equal(link.tagName, "A");
    assert.equal(link.href, "https://bmkg.go.id/cuaca");
    assert.equal(link.rel, "noopener noreferrer");
    assert.equal(link.target, "_blank");
    assert.equal(link.textContent, "https://bmkg.go.id/cuaca");
  });

  it("hands a clicked link to onLink instead of following it", () => {
    const opened = [];
    const [, , , , source] = elements(buildCard(fakeDocument, card, { onLink: (url) => opened.push(url) }));

    const prevented = elements(source)[0].click();

    assert.equal(prevented, true);
    assert.deepEqual(opened, ["https://bmkg.go.id/cuaca"]);
  });

  it("follows a link like any other without onLink", () => {
    const [, , , , source] = elements(buildCard(fakeDocument, card));

    assert.equal(elements(source)[0].listeners.size, 0);
  });

  it("puts markup in the card on the page as plain text", () => {
    const hostile = '<img src=x onerror="alert(1)"> <a href="javascript:alert(1)">klik</a>';

    const root = buildCard(fakeDocument, { title: "<b>Judul</b>", body: `- ${hostile}` });

    const created = [...walk(root)].filter((node) => node.nodeType === 1).map((node) => node.tagName);
    assert.deepEqual(created, ["DIV", "H2", "UL", "LI"]);
    assert.equal(root.textContent, `<b>Judul</b>${hostile}`);
  });
});

// --- No HTML is ever parsed from text on the pages ----------------------------------------------------------------------

describe("the pages that show cards", () => {
  for (const file of ["phone/card.js", "index.html", "phone/phone.js"]) {
    it(`${file} never parses HTML from text`, () => {
      const source = readFileSync(new URL(file, WEB), "utf8");

      assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
    });
  }
});
