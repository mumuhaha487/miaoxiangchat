import type { Conversation } from './types';

const greetingPattern = /^(你好|您好|嗨|哈喽|hello|hi)[！!。.，,？?\s]*$/i;
const thanksPattern = /^(谢谢|感谢|多谢|辛苦了)[！!。.，,？?\s]*$/;

function clipTitle(value: string, limit = 28) {
  const characters = Array.from(value.trim());
  return characters.length <= limit ? characters.join('') : `${characters.slice(0, limit).join('')}…`;
}

export function summarizeConversationTitle(content: string) {
  const normalized = String(content || '')
    .replace(/@<[^>]+>/g, ' ')
    .replace(/https?:\/\/\S+/gi, '网页链接')
    .replace(/\s+/g, ' ')
    .trim();
  if (!normalized) return '新对话';
  if (greetingPattern.test(normalized)) return '用户问候';
  if (thanksPattern.test(normalized)) return '用户致谢';
  if (/^(在吗|你在吗|有人吗)[？?！!。.\s]*$/.test(normalized)) return '询问是否在线';
  if (/(新闻|热点)/.test(normalized) && /(最近|近期|今日|今天|目前|看看|查询|搜索|哪些)/.test(normalized)) {
    return '查询最近的新闻热点';
  }

  let title = normalized
    .replace(/^(?:请你?|麻烦你?|劳驾|可以|能不能|能否|我想让你|我需要你|帮我|帮忙)(?:先|再|一下|看看)?\s*/u, '')
    .replace(/^(?:给我|为我)\s*/u, '')
    .replace(/[。！？!?；;，,]+$/u, '')
    .trim();
  if (/^(?:看看|查询|搜索|查找|检索)/u.test(title)) {
    title = title.replace(/^(?:看看|查询|搜索|查找|检索)(?:一下)?/u, '查询');
  } else if (/^(?:写|写一个|写一份|制作|做一个|生成)/u.test(title) && /ppt|演示文稿/i.test(title)) {
    title = `制作${title.replace(/^(?:写|写一个|写一份|制作|做一个|生成)(?:有关|关于)?/u, '')}`;
  }
  return clipTitle(title || normalized);
}

function localDayStart(timestamp: number) {
  const date = new Date(timestamp || Date.now());
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

export function conversationDateLabel(timestamp: number, now = Date.now()) {
  const difference = Math.round((localDayStart(now) - localDayStart(timestamp)) / 86_400_000);
  if (difference <= 0) return '今天';
  if (difference === 1) return '昨天';
  if (difference === 2) return '前天';
  const date = new Date(timestamp);
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

export function groupConversationsByDate(conversations: Conversation[]) {
  const groups: Array<{ label: string; conversations: Conversation[] }> = [];
  const ordered = [...conversations].sort((left, right) => right.updatedAt - left.updatedAt);
  for (const conversation of ordered) {
    const label = conversationDateLabel(conversation.updatedAt || conversation.createdAt);
    const existing = groups.find((group) => group.label === label);
    if (existing) existing.conversations.push(conversation);
    else groups.push({ label, conversations: [conversation] });
  }
  return groups;
}
