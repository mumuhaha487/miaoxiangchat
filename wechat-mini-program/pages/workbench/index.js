const { API_ORIGIN } = require('../../utils/config');

Page({
  data: {
    src: '',
    loading: true,
    error: '',
  },

  onLoad(options) {
    const ticket = String(options.ticket || '').trim();
    if (!ticket) {
      wx.reLaunch({ url: '/pages/auth/index' });
      return;
    }
    this.setData({
      src: `${API_ORIGIN}/app-shell/android-3.8.1/index.html?client=wechat-mini-program&ui=android-3.8.1&handoff=${encodeURIComponent(ticket)}#wechat_redirect`,
    });
  },

  loaded() {
    this.setData({ loading: false, error: '' });
  },

  failed(event) {
    const detail = event.detail || {};
    this.setData({ loading: false, error: detail.errMsg || '完整工作台加载失败' });
  },
});
