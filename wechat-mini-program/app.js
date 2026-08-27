const { getToken } = require('./utils/storage');
const { WECHAT_CLOUD_ENV_ID } = require('./utils/config');

App({
  globalData: { token: getToken() },

  onLaunch() {
    if (wx.cloud && typeof wx.cloud.init === 'function') {
      wx.cloud.init({ env: WECHAT_CLOUD_ENV_ID, traceUser: true });
    }
    if (!wx.canIUse('getUpdateManager')) return;
    const updateManager = wx.getUpdateManager();
    updateManager.onUpdateReady(() => {
      wx.showModal({
        title: '新版本已就绪',
        content: '重新启动后即可使用最新版本。',
        confirmText: '立即更新',
        success: ({ confirm }) => { if (confirm) updateManager.applyUpdate(); },
      });
    });
  },
});
