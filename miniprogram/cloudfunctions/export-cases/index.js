/**
 * 全站导出（硬功能，服务端校验权益）。
 *
 * 与深度报告（软墙）不同：导出是**计算功能**，白嫖成本为零
 * （CSV 拿到就能扩散），所以权益校验必须落在服务端 ——
 * 前端按钮只是入口，不是边界。
 *
 * 产出：CSV 文本（前端复制到剪贴板）+ 云存储文件（返回 fileID 备用）。
 * 注意 wx.openDocument 不支持 csv，所以主路径是剪贴板。
 */

const cloud = require('wx-server-sdk');
const { getEntitlements } = require('./lib/deliver.js');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

/** CSV 列 —— 与 get-cases 白名单同源，口径列必须带（红线 1） */
const COLS = [
  ['id', 'id'],
  ['name', 'name'],
  ['one_liner', '一句话定位'],
  ['origin', '国家/地区'],
  ['category', '分类'],
  ['tier', '分档'],
  ['verification', '收入验证'],
  ['mrr', 'MRR(美元/月)'],
  ['last_30d_revenue', '近30天收入(美元)'],
  ['arr', 'ARR(美元/年)'],
  ['all_time', '累计收入(美元)'],
  ['caliber', '口径标记'],
  ['solo_score', '一个人做得来'],
  ['china_score', '搬回国内'],
  ['composite_score', '综合分'],
  ['quadrant', '象限'],
  ['updated_at', '更新日期']
];

exports.main = async (event) => {
  const openid = cloud.getWXContext().OPENID;
  if (!openid) {
    return { errcode: 40001, errmsg: '未登录，请重新进入小程序' };
  }

  const ent = await getEntitlements(db, openid);
  const isMember = ent.member_until && ent.member_until > Date.now();
  if (!ent.export_all && !isMember) {
    return { errcode: 40301, errmsg: '导出为付费功能：请先购买「全站案例导出」或开通月度会员' };
  }

  const r = await db.collection('cases').limit(1000).get();
  const list = r.data || [];

  // ---- 组 CSV ----
  const esc = (v) => {
    if (v == null) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [COLS.map((c) => esc(c[1])).join(',')];
  for (const c of list) {
    lines.push(COLS.map((pair) => esc(c[pair[0]])).join(','));
  }
  const csv = lines.join('\n');

  // ---- 顺手存一份到云存储（fileID 备用：大内容剪贴板不稳时用） ----
  let fileId = '';
  try {
    const up = await cloud.uploadFile({
      cloudPath: 'exports/' + openid.slice(0, 10) + '-' + Date.now() + '.csv',
      fileContent: Buffer.from(csv, 'utf8')
    });
    fileId = up.fileID || '';
  } catch (e) {
    console.warn('uploadFile 失败（不影响剪贴板路径）', e);
  }

  return { errcode: 0, csv: csv, fileId: fileId, count: list.length };
};
