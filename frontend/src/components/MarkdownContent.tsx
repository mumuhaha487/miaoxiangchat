import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Props {
  content: string;
  className?: string;
}

export function MarkdownContent({ content, className = '' }: Props) {
  return (
    <div className={`markdownContent ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, ...props }) => (
            <a
              {...props}
              href={href}
              target={href?.startsWith('#') ? undefined : '_blank'}
              rel={href?.startsWith('#') ? undefined : 'noopener noreferrer'}
            />
          ),
          img: ({ alt, ...props }) => (
            <img {...props} alt={alt || ''} loading="lazy" referrerPolicy="no-referrer" />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
