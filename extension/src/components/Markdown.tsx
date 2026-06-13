import { ReactNode, JSX } from "react";

type MarkdownProps = {
  content: string;
};

export function Markdown({ content }: MarkdownProps) {
  if (!content) return null;

  // Split by line and process block elements
  const lines = content.split("\n");
  const elements: ReactNode[] = [];
  let currentList: ReactNode[] = [];
  let currentListType: "ul" | "ol" | null = null;
  let inCodeBlock = false;
  let codeBlockContent: string[] = [];
  let codeBlockLang = "";

  const flushList = (key: string) => {
    if (currentList.length > 0) {
      if (currentListType === "ul") {
        elements.push(<ul key={`ul-${key}`} className="md-ul">{[...currentList]}</ul>);
      } else {
        elements.push(<ol key={`ol-${key}`} className="md-ol">{[...currentList]}</ol>);
      }
      currentList = [];
      currentListType = null;
    }
  };

  const renderInline = (text: string): ReactNode[] => {
    const parts: ReactNode[] = [];
    let remaining = text;
    let index = 0;

    // Matches bold: **text**, inline code: `code`, link: [label](url), italic: *text*
    while (remaining.length > 0) {
      const boldMatch = /^\*\*([^*]+)\*\*/.exec(remaining);
      const codeMatch = /^`([^`]+)`/.exec(remaining);
      const italicMatch = /^\*([^*]+)\*/.exec(remaining);
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)/.exec(remaining);

      if (boldMatch) {
        parts.push(<strong key={`b-${index}`}>{boldMatch[1]}</strong>);
        remaining = remaining.slice(boldMatch[0].length);
      } else if (codeMatch) {
        parts.push(<code key={`c-${index}`} className="md-inline-code">{codeMatch[1]}</code>);
        remaining = remaining.slice(codeMatch[0].length);
      } else if (italicMatch) {
        parts.push(<em key={`i-${index}`}>{italicMatch[1]}</em>);
        remaining = remaining.slice(italicMatch[0].length);
      } else if (linkMatch) {
        parts.push(
          <a key={`l-${index}`} href={linkMatch[2]} target="_blank" rel="noopener noreferrer" className="md-link">
            {linkMatch[1]}
          </a>
        );
        remaining = remaining.slice(linkMatch[0].length);
      } else {
        // Find the next token start
        const nextTokenIndex = remaining.search(/\*\*|`|\*|\[/);
        if (nextTokenIndex === -1) {
          parts.push(remaining);
          break;
        } else if (nextTokenIndex > 0) {
          parts.push(remaining.slice(0, nextTokenIndex));
          remaining = remaining.slice(nextTokenIndex);
        } else {
          // Stray match, consume 1 character
          parts.push(remaining[0]);
          remaining = remaining.slice(1);
        }
      }
      index++;
    }

    return parts;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code block check
    if (line.startsWith("```")) {
      if (inCodeBlock) {
        // End of code block
        elements.push(
          <pre key={`code-${i}`} className="md-code-block">
            {codeBlockLang ? <div className="md-code-lang">{codeBlockLang}</div> : null}
            <code>{codeBlockContent.join("\n")}</code>
          </pre>
        );
        inCodeBlock = false;
        codeBlockContent = [];
        codeBlockLang = "";
      } else {
        // Start of code block
        inCodeBlock = true;
        codeBlockLang = line.slice(3).trim();
      }
      continue;
    }

    if (inCodeBlock) {
      codeBlockContent.push(line);
      continue;
    }

    // Bullet list match (* or - or +)
    const ulMatch = /^[*-+]\s+(.*)$/.exec(line);
    if (ulMatch) {
      if (currentListType && currentListType !== "ul") {
        flushList(String(i));
      }
      currentListType = "ul";
      currentList.push(<li key={`li-${i}`}>{renderInline(ulMatch[1])}</li>);
      continue;
    }

    // Numbered list match (e.g. 1. item)
    const olMatch = /^\d+\.\s+(.*)$/.exec(line);
    if (olMatch) {
      if (currentListType && currentListType !== "ol") {
        flushList(String(i));
      }
      currentListType = "ol";
      currentList.push(<li key={`li-${i}`}>{renderInline(olMatch[1])}</li>);
      continue;
    }

    // If it's not a list item, flush any active list
    if (currentList.length > 0) {
      flushList(String(i));
    }

    // Header match
    const headerMatch = /^(#{1,6})\s+(.*)$/.exec(line);
    if (headerMatch) {
      const level = headerMatch[1].length;
      const text = headerMatch[2];
      const HeaderTag = `h${level}` as keyof JSX.IntrinsicElements;
      elements.push(<HeaderTag key={`h-${i}`} className={`md-h${level}`}>{renderInline(text)}</HeaderTag>);
      continue;
    }

    // Blank line -> line break/spacing
    if (line.trim() === "") {
      elements.push(<div key={`br-${i}`} className="md-spacing" />);
      continue;
    }

    // Regular paragraph line
    elements.push(<p key={`p-${i}`} className="md-p">{renderInline(line)}</p>);
  }

  // Flush remaining lists
  if (currentList.length > 0) {
    flushList("end");
  }

  return <div className="markdown-body">{elements}</div>;
}
