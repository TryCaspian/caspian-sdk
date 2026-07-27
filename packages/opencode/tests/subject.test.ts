import { describe, expect, test } from "bun:test";
import {
  inferSubjectFromBody,
  isBlankSubject,
  normalizeOutboundSubject,
  replySubject,
  resolveInboundSubject,
  stripReplyPrefixes,
} from "../src/subject.ts";

describe("subject helpers", () => {
  test("isBlankSubject catches bare Re:", () => {
    expect(isBlankSubject(undefined)).toBe(true);
    expect(isBlankSubject("")).toBe(true);
    expect(isBlankSubject("(no subject)")).toBe(true);
    expect(isBlankSubject("Re:")).toBe(true);
    expect(isBlankSubject("Re: ")).toBe(true);
    expect(isBlankSubject("RE:")).toBe(true);
    expect(isBlankSubject("Re: The Zen of Python")).toBe(false);
  });

  test("inferSubjectFromBody finds quoted Subject", () => {
    const body = `What is the zen of rust?\n\nOn Sat wrote:\n> Subject: The Zen of Python\n>\n> Beautiful is better`;
    expect(inferSubjectFromBody(body)).toBe("The Zen of Python");
  });

  test("resolveInboundSubject prefers header then body", () => {
    expect(resolveInboundSubject("Re: Hello")).toBe("Re: Hello");
    expect(
      resolveInboundSubject("Re:", "> Subject: The Zen of Python\n> hi"),
    ).toBe("The Zen of Python");
    expect(resolveInboundSubject("Re:")).toBe("(no subject)");
  });

  test("normalizeOutboundSubject requires real subject for new mail", () => {
    expect(normalizeOutboundSubject("Re:")).toBeUndefined();
    expect(normalizeOutboundSubject("Zen of Rust")).toBe("Zen of Rust");
    expect(
      normalizeOutboundSubject("Re:", { threadBase: "The Zen of Python" }),
    ).toBe("The Zen of Python");
    expect(
      normalizeOutboundSubject("Re:", {
        threadBase: "The Zen of Python",
        asReply: true,
      }),
    ).toBe("Re: The Zen of Python");
  });

  test("replySubject / stripReplyPrefixes", () => {
    expect(stripReplyPrefixes("Re: Re: Hi")).toBe("Hi");
    expect(replySubject("The Zen of Python")).toBe("Re: The Zen of Python");
    expect(replySubject("Re:")).toBeUndefined();
  });
});
