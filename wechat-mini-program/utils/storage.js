const KEYS = {
  token: 'mumu.wechat.token.v1',
  trustToken: 'mumu.wechat.trust.v1',
  deviceId: 'mumu.wechat.device.v1',
  guestArchive: 'mumu.wechat.guest.v1',
  selectedConversation: 'mumu.wechat.selected.v1',
  notificationCursor: 'mumu.wechat.notification.cursor.v1',
  accountArchive: 'mumu.wechat.account.archive.v1',
};

function read(key, fallback = '') {
  try {
    const value = wx.getStorageSync(key);
    return value === undefined || value === null || value === '' ? fallback : value;
  } catch (_error) {
    return fallback;
  }
}

function write(key, value) {
  try {
    if (value === undefined || value === null || value === '') wx.removeStorageSync(key);
    else wx.setStorageSync(key, value);
  } catch (_error) {
    // Storage failures fall back to a non-trusted, one-session login.
  }
}

function getToken() {
  return String(read(KEYS.token, ''));
}

function setToken(token) {
  write(KEYS.token, String(token || ''));
  const app = getApp({ allowDefault: true });
  if (app && app.globalData) app.globalData.token = String(token || '');
}

function clearSession() {
  write(KEYS.token, '');
}

module.exports = {
  KEYS,
  read,
  write,
  getToken,
  setToken,
  clearSession,
};
