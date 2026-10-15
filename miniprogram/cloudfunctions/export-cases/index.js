/**
 * 全站导出（硬功能，服务端校验权益）。
 *
 * 与深度报告同属付费内容，但**导出必须硬校验**：
 * CSV 拿到就能扩散，前端按钮只是入口，不是边界。
 *
 * 2026-10-15：内容收费后，导出的每一列都在 get-cases 的 PAID 层里
 * （mrr / last_30d_revenue / arr / all_time / caliber 等），
 * 所以这道门是唯一入口 —— 判定必须与 get-cases 的 canReadFull 同一套规则，
 * 不在这里另写一份，避免两处漂移出「买了报告却导不出」这种反直觉 bug。
 */

const cloud = require('wx-server-sdk');
const { getEntitlements, canReadFull } = require('./lib/deliver.js');

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
  if (!canReadFull(ent, '')) {
    return {
      errcode: 40301,
      errmsg: '导出为付费功能：请先购买「全站案例导出」、开通月度会员，或购买任一篇深度报告'
    };
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
