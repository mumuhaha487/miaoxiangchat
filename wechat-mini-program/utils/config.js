// Replace these public examples before building the mini program.
const API_ORIGIN = 'https://example.com';
const WECHAT_CLOUD_ENV_ID = 'your-cloud-environment-id';

module.exports = {
  API_ORIGIN,
  API_BASE: `${API_ORIGIN}/api/v1`,
  WECHAT_CLOUD_ENV_ID,
  WECHAT_CLOUD_LOGIN_FUNCTION: 'wechatLogin',
  APP_NAME: '妙想之地',
  MAX_ATTACHMENTS: 8,
  NOTIFICATION_POLL_MS: 10000,
  TASK_POLL_MS: 1800,
  // Add template IDs from 微信公众平台 -> 功能 -> 订阅消息 before enabling background pushes.
  SUBSCRIPTION_TEMPLATE_IDS: [],
};
