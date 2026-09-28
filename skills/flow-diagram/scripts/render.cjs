#!/usr/bin/env node
// 図の HTML を PNG に撮る。全体 1 枚 (full.png) と、id を持つ section ごとに 1 枚ずつ撮る。ライトとダークの両方で撮る。
// 文章の点検にかけられるよう、表示される文章を text.txt にも書き出す。
// 撮る id を決め打ちしない。section[id] を DOM から拾うので、図の構成を変えてもスクリプトは変えなくていい。
//
// Usage: node render.cjs <html-path> <out-dir>
//
// playwright の解決順:
//   1. require('playwright') (グローバル install 済み、または NODE_PATH 指定)
//   2. 見つからなければ、NODE_PATH の設定方法をエラーメッセージで案内して終了する

const fs = require('fs');
const path = require('path');

const WIDTH = 1100;
const PADDING = 32;
const SCALE = 2; // 文字を拡大表示しても潰れない解像度にするための倍率

function loadPlaywright() {
  try {
    return require('playwright');
  } catch (err) {
    console.error('playwright が見つかりません。次のいずれかで解決してください。');
    console.error('  1. 既存の pnpm store を NODE_PATH に指定する');
    console.error('     例: NODE_PATH=<repo>/node_modules/.pnpm/playwright@<version>/node_modules node scripts/render.cjs ...');
    console.error('     バージョンは `find <repo>/node_modules/.pnpm -maxdepth 1 -iname "playwright@*"` で確認する');
    console.error('  2. npx 経由で実行する: npx -y playwright ... は CLI 専用なので、代わりに');
    console.error('     `npm exec -y --package=playwright -- node scripts/render.cjs <html-path> <out-dir>` を使う');
    process.exit(1);
  }
}

async function main() {
  const [, , htmlPath, outDir] = process.argv;
  if (!htmlPath || !outDir) {
    console.error('Usage: node render.cjs <html-path> <out-dir>');
    process.exit(1);
  }
  const absHtml = path.resolve(htmlPath);
  if (!fs.existsSync(absHtml)) {
    console.error(`html が見つかりません: ${absHtml}`);
    process.exit(1);
  }
  fs.mkdirSync(outDir, { recursive: true });

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: WIDTH, height: 900 },
    deviceScaleFactor: SCALE,
  });
  await page.goto('file://' + absHtml);
  await page.waitForTimeout(800); // Web フォント読み込みの完了待ち

  // GitHub は閲覧者の配色設定で <picture> の画像を切り替えられるので、ライト (<id>.png) とダーク (<id>-dark.png) の両方を撮る。
  // 撮影はこのスクリプトの中で終わるため、枚数が増えても確認の手間は増えない (確認で開くのはダークだけでよい)
  for (const scheme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.waitForTimeout(200);
    const suffix = scheme === 'dark' ? '-dark' : '';

    const fullPath = path.join(outDir, `full${suffix}.png`);
    await page.screenshot({ path: fullPath, fullPage: true });
    console.log(`✓ ${fullPath}`);

    const ids = await page.$$eval('section[id]', (els) => els.map((el) => el.id));
    for (const id of ids) {
      const shotPath = path.join(outDir, `${id}${suffix}.png`);
      // 節の外周ぎりぎりで切ると見出しや枠が画像の端に貼り付くので、ページの背景ごと余白を付けて撮る
      const box = await page.locator(`#${id}`).boundingBox();
      const x = Math.max(0, box.x - PADDING);
      const y = Math.max(0, box.y - PADDING);
      await page.screenshot({
        path: shotPath,
        fullPage: true,
        clip: { x, y, width: box.width + (box.x - x) + PADDING, height: box.height + (box.y - y) + PADDING },
      });
      console.log(`✓ ${shotPath}`);
    }
  }

  // 文章の点検は段落単位で判定するため、見出し・段落・行ごとに空行で区切って書き出す
  const TEXT_BLOCKS = 'h1, h2, h3, p, .row, .step, .end, .mark, .race > div, .actor, .cast > div';
  const text = await page.$$eval(TEXT_BLOCKS, (els, sel) =>
    els
      // 入れ子になった区切り (カードとその中の段落など) は内側だけを使い、同じ文を二重に出さない
      .filter((el) => !el.closest('.lightbox') && !el.querySelector(sel))
      .map((el) => el.innerText.replace(/\s*\n\s*/g, ' ').trim())
      .filter(Boolean)
      .join('\n\n'),
    TEXT_BLOCKS,
  );
  const textPath = path.join(outDir, 'text.txt');
  fs.writeFileSync(textPath, text + '\n');
  console.log(`✓ ${textPath}`);

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
