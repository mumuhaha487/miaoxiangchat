const api = require('../../utils/api');
const { deviceContext, persistTrustToken } = require('../../utils/device');
const { clearSession, getToken, setToken } = require('../../utils/storage');

function wechatSession() {
  return new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
}

function redirectToWorkbench(ticket) {
  return new Promise((resolve, reject) => {
    wx.redirectTo({
      url: `/pages/workbench/index?ticket=${encodeURIComponent(ticket)}`,
      success: resolve,
      fail: reject,
    });
  });
}

Page({
  data: {
    busy: false,
    error: '',
  },

  async onLoad() {
    await this.authenticate();
  },

  async authenticate() {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '' });
    try {
      if (!getToken()) {
        const login = await wechatSession();
        if (!login.code) throw new Error('微信未返回登录凭证');
        const result = await api.auth.wechatLogin(deviceContext());
        if (!result.token) throw new Error('服务器未返回登录凭证');
        setToken(result.token);
        persistTrustToken(result.deviceCredential || '');
      }
      const ticket = await api.auth.webviewTicket();
      if (!ticket.ticket) throw new Error('服务器未返回工作台票据');
      await redirectToWorkbench(ticket.ticket);
    } catch (error) {
      clearSession();
      this.setData({
        busy: false,
        error: error.message || error.errMsg || '微信登录失败，请重试',
      });
    }
  },
});
