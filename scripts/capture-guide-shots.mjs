/**
 * 첫 접속 사용 가이드(`src/components/OnboardingGuide.tsx`)에 쓰는 화면 사진을 찍는다.
 *
 * 화면이 바뀌면 사진이 거짓말이 되므로 다시 찍을 수 있어야 한다. 손으로 찍으면
 * 매번 크기·자르는 위치가 달라져서, 설치형 도구 없이 재현되게 만들었다.
 *
 * 준비: 개발 서버(`npm run dev`)와 백엔드(8000)가 떠 있어야 한다.
 * 실행: node scripts/capture-guide-shots.mjs
 *
 * macOS 에 이미 있는 Chrome 을 헤드리스로 띄우고 CDP 로 직접 조작한다.
 * puppeteer/playwright 를 넣지 않은 이유는 브라우저를 또 내려받게 되기 때문이다.
 * Node 22+ 의 내장 WebSocket 을 쓰므로 의존성이 없다.
 */
import { spawn } from 'node:child_process';
import { mkdir, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, '..');
const outputDirectory = path.join(projectRoot, 'public', 'guide');

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9333;
const APP_URL = 'http://localhost:5173';
// 16:10. 가이드 팝업의 사진 칸이 같은 비율이라 잘리는 부분이 없다.
const WIDTH = 1440;
const HEIGHT = 900;
// 자르는 위치와 최종 크기는 guide-shots-to-webp.py 가 정한다. 여기서는 원본만 남긴다.

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function findPageSocket() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const targets = await response.json();
      const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch {
      // 아직 안 떴다. 다시 시도한다.
    }
    await sleep(300);
  }
  throw new Error('Chrome 디버깅 포트에 붙지 못했습니다.');
}

function createClient(socket) {
  let lastId = 0;
  const pending = new Map();

  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  };

  return (method, params = {}) =>
    new Promise((resolve, reject) => {
      lastId += 1;
      pending.set(lastId, { resolve, reject });
      socket.send(JSON.stringify({ id: lastId, method, params }));
    });
}

// 페이지에 심어두는 조작 도구. React 입력은 native setter 로 값을 넣어야 반응한다.
const PAGE_HELPERS = `
window.__guide = {
  sleep: (ms) => new Promise(r => setTimeout(r, ms)),
  gu(name) {
    const el = document.querySelector('.heatControlBlock select');
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set.call(el, name);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  },
  async grid(code) {
    const input = [...document.querySelectorAll('input[type=text]')]
      .find((i) => i.placeholder.includes('격자 코드'));
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, code);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.closest('div').querySelectorAll('button')
      .forEach((b) => { if (b.textContent.trim() === '검색') b.click(); });
    await this.sleep(3500);
  },
  indicator(label) {
    const button = [...document.querySelectorAll('.indicatorList button')]
      .find((b) => b.textContent.includes(label));
    if (button) button.click();
  }
};
'ok'`;

async function main() {
  await mkdir(outputDirectory, { recursive: true });
  const profileDirectory = path.join(process.env.TMPDIR ?? '/tmp', 'gaon-guide-shots');
  await rm(profileDirectory, { recursive: true, force: true });

  const chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profileDirectory}`,
    `--window-size=${WIDTH},${HEIGHT}`,
    '--hide-scrollbars',
    '--force-device-scale-factor=3',
    '--no-first-run',
    '--no-default-browser-check',
    APP_URL
  ]);
  chrome.stderr.on('data', () => {});

  const socket = new WebSocket(await findPageSocket());
  await new Promise((resolve) => {
    socket.onopen = resolve;
  });
  const send = createClient(socket);

  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };

  const capture = async (name) => {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    await writeFile(path.join(outputDirectory, `${name}.png`), Buffer.from(data, 'base64'));
    console.log('찍음:', name);
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: WIDTH,
    height: HEIGHT,
    deviceScaleFactor: 3,
    mobile: false
  });

  // 가이드 팝업 자신이 첫 접속에 뜨므로 본 것으로 표시한다. 챗봇 말풍선도 같이
  // 꺼야 한다 — 가이드를 본 사람으로 인식돼 바로 떠서 사진마다 찍혀 들어간다.
  await evaluate(
    `localStorage.setItem('gaon_onboarding_seen','1');` +
      ` localStorage.setItem('gaon_chat_hint_seen','1');` +
      ` location.href='${APP_URL}'`
  );
  await sleep(6000);
  await evaluate(PAGE_HELPERS);
  await sleep(3000);

  await capture('step-1-overview');

  await evaluate(
    `(async () => { window.__guide.gu('강남구'); await window.__guide.sleep(2500);` +
      ` await window.__guide.grid('11680_01400'); return 'ok'; })()`
  );
  await sleep(3000);
  await capture('step-2-grid');

  await evaluate(`window.__guide.indicator('녹지율'); 'ok'`);
  await sleep(2500);
  await capture('step-3-indicator');

  await evaluate(`
    (async () => {
      window.__guide.indicator('개선 우선순위');
      await window.__guide.sleep(1200);
      document.querySelector('.gdpSimJump')?.click();
      await window.__guide.sleep(900);
      document.querySelectorAll('.policyPresetButton')[0]?.click();
      await window.__guide.sleep(1200);
      const card = document.getElementById('gdpSimCard');
      const scroller = card?.closest('.rightPanelDashboard');
      if (card && scroller) {
        const scale = card.getBoundingClientRect().height / card.offsetHeight || 1;
        scroller.scrollTop +=
          (card.getBoundingClientRect().top - scroller.getBoundingClientRect().top) / scale - 8;
      }
      return 'ok';
    })()`);
  await sleep(2500);
  await capture('step-4-simulation');

  // 챗봇 창을 연다. 답변까지 받으려면 LLM 이 필요하므로, 처음 열었을 때의
  // 사용 가이드와 예시 질문이 보이는 상태를 찍는다.
  await evaluate(`
    (async () => {
      document.querySelector('.aiChatHintClose')?.click();
      document.querySelector('.aiChatFab')?.click();
      await window.__guide.sleep(1500);
      return 'ok';
    })()`);
  await sleep(2500);
  await capture('step-5-chat');

  socket.close();
  chrome.kill();

  console.log('\nPNG 를 public/guide/ 에 남겼습니다. WebP 로 바꾸세요:');
  console.log('  python3 scripts/guide-shots-to-webp.py');
}

await main();
process.exit(0);
