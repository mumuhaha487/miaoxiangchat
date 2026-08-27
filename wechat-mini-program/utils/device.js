const { KEYS, read, write } = require('./storage');

function randomId() {
  let value = '';
  for (let index = 0; index < 32; index += 1) {
    value += Math.floor(Math.random() * 16).toString(16);
  }
  return `${Date.now().toString(36)}-${value}`;
}

function deviceContext() {
  let deviceId = String(read(KEYS.deviceId, ''));
  if (!deviceId) {
    deviceId = randomId();
    write(KEYS.deviceId, deviceId);
  }

  let info = {};
  try {
    info = wx.canIUse('getDeviceInfo') ? wx.getDeviceInfo() : wx.getSystemInfoSync();
  } catch (_error) {
    info = {};
  }
  const brand = String(info.brand || '').trim();
  const model = String(info.model || '').trim();
  const deviceName = [brand, model].filter(Boolean).join(' ') || '微信小程序';

  return {
    device_id: deviceId,
    device_name: deviceName.slice(0, 120),
    client_platform: 'wechat',
    trust_token: String(read(KEYS.trustToken, '')),
  };
}

function persistTrustToken(token) {
  write(KEYS.trustToken, String(token || ''));
}

module.exports = { deviceContext, persistTrustToken };
