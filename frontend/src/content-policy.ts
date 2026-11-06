import type { SanitizeHtmlOptions } from '@particle-academy/react-fancy';

/** Configure Fancy's published parser/sanitizer; prose cannot supply active UI. */
export const CHAT_PROSE: SanitizeHtmlOptions = {
  allowedTags: ['p', 'br', 'strong', 'em', 'del', 'blockquote', 'ul', 'ol', 'li', 'pre', 'code',
    'h1', 'h2', 'h3', 'h4', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'a'],
  allowedAttributes: { a: ['href', 'title'] },
  allowedSchemes: ['https:', 'http:', 'mailto:'],
  allowRelative: true,
};
