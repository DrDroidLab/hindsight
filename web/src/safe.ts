import MarkdownIt from "markdown-it";
export const escape = (value: unknown): string =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const renderer = new MarkdownIt({ html: false, linkify: false, breaks: true });
// Never fetch images embedded in untrusted knowledge. Links are references only.
renderer.renderer.rules.image = (tokens, index) =>
  `[Image omitted: ${escape(tokens[index].content)}]`;
renderer.renderer.rules.link_open = (tokens, index, options, _env, self) => {
  tokens[index].attrSet("rel", "noopener noreferrer");
  tokens[index].attrSet("target", "_blank");
  return self.renderToken(tokens, index, options);
};
// Raw HTML stays escaped and markdown-it rejects javascript/data/vbscript URLs.
// Evidence and diffs use escape(), never this Markdown-rendering function.
export const markdown = (value: string): string => renderer.render(value);
