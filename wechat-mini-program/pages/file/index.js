const { API_ORIGIN } = require('../../utils/config');
const { getToken } = require('../../utils/storage');

Page({
  data: {
    filename: '文件',
    status: '正在准备文件...',
    error: '',
    localPath: '',
    busy: true,
  },

  onLoad(options) {
    const conversationId = String(options.conversationId || '').trim();
    const path = String(options.path || '').trim();
    const filename = String(options.filename || '文件').replace(/[\\/]/g, '_').slice(0, 240) || '文件';
    const token = getToken();
    this.setData({ filename });
    if (!conversationId || !path || !token || path.includes('..')) {
      this.setData({ busy: false, error: '文件授权无效，请返回工作台后重试。', status: '' });
      return;
    }
    this.downloadUrl = `${API_ORIGIN}/api/v1/workspace/download?conversation_id=${encodeURIComponent(conversationId)}&path=${encodeURIComponent(path)}`;
    this.download(token, true);
  },

  download(token = getToken(), shareAfter = false) {
    if (!this.downloadUrl || !token || this.data.busy && this.data.localPath) return;
    this.setData({ busy: true, error: '', status: '正在下载文件...' });
    wx.downloadFile({
      url: this.downloadUrl,
      header: { Authorization: `Bearer ${token}` },
      success: (result) => {
        if (result.statusCode !== 200 || !result.tempFilePath) {
          this.setData({ busy: false, status: '', error: `文件下载失败 (${result.statusCode || 0})` });
          return;
        }
        this.setData({ localPath: result.tempFilePath, busy: false, status: '文件已准备好' });
        if (shareAfter) this.shareNow();
      },
      fail: () => this.setData({ busy: false, status: '', error: '文件下载失败，请检查网络后重试。' }),
    });
  },

  shareNow() {
    const filePath = this.data.localPath;
    if (!filePath || !wx.canIUse('shareFileMessage')) {
      this.setData({ error: '当前微信版本不支持文件分享，请升级微信后重试。' });
      return;
    }
    this.setData({ busy: true, error: '', status: '正在打开微信分享...' });
    wx.shareFileMessage({
      filePath,
      fileName: this.data.filename,
      success: () => this.setData({ busy: false, status: '文件已发送' }),
      fail: (reason) => {
        const cancelled = String(reason.errMsg || '').toLowerCase().includes('cancel');
        this.setData({ busy: false, status: cancelled ? '已取消分享' : '', error: cancelled ? '' : '未能打开文件分享，请重试。' });
      },
    });
  },

  retry() {
    if (this.data.localPath) this.shareNow();
    else this.download(getToken(), true);
  },
});
