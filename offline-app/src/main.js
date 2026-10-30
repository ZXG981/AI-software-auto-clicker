import * as webllm from "@mlc-ai/web-llm";
import Tesseract from "tesseract.js";
import "./style.css";

const $ = selector => document.querySelector(selector);
let COURSE_DATA = null;
let agentNames = {
  cet4: "四级阅读与写作",
  cet6: "六级阅读与写作",
  postgrad: "考研阅读与写作",
};
let engine = null;
let loadedModel = "";
let modelReady = false;
let selectedPhoto = null;
let photoUrl = null;
let ocrWorker = null;
let installPrompt = null;
let updateRegistration = null;
let pendingUpdateReload = false;
const APP_VERSION = "2026.10.30.2";

function decodeBase64(value) {
  return Uint8Array.from(atob(value), char => char.charCodeAt(0));
}

async function decryptCourseData(passphrase) {
  const response = await fetch(new URL("./course-data.enc.json", window.location.href), {
    cache: "no-cache",
  });
  if (!response.ok) throw new Error("无法读取加密课程包");
  const encrypted = await response.json();
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(passphrase),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  const key = await crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: decodeBase64(encrypted.salt),
      iterations: encrypted.iterations,
      hash: "SHA-256",
    },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: decodeBase64(encrypted.nonce) },
    key,
    decodeBase64(encrypted.ciphertext),
  );
  return JSON.parse(new TextDecoder().decode(plaintext));
}

async function unlockCourseData(passphrase) {
  COURSE_DATA = await decryptCourseData(passphrase);
  agentNames = COURSE_DATA.agents;
  sessionStorage.setItem("courseDataPassphrase", passphrase);
  $("#unlockOverlay").hidden = true;
  $("#unlockError").textContent = "";
  renderCourseList();
}

function resetModelState(messageText) {
  modelReady = false;
  engine = null;
  loadedModel = "";
  $("#askButton").disabled = true;
  $("#askButton").textContent = "请重新加载模型";
  $("#modelProgress").style.width = "0";
  $("#modelStatus").textContent = messageText;
}

const offlineAppConfig = {
  ...webllm.prebuiltAppConfig,
  model_list: webllm.prebuiltAppConfig.model_list.map(model => {
    const commits = {
      "Qwen3-0.6B-q4f16_1-MLC": "8c14ce481d4c692769976ad52afea453a102df19",
      "Qwen3-1.7B-q4f16_1-MLC": "80b3abcec6c3b3f5355dc0cc99cc4fb578f192bc",
      "Qwen3-4B-q4f16_1-MLC": "a5c9fab855e3ccbdfed2e7e69683d75f30332161",
    };
    const commit = commits[model.model_id];
    const localProxyBase = new URL(
      `./model-files/${model.model_id}/resolve/main/`,
      window.location.href,
    ).href;
    return {
      ...model,
      model:
        commit && model.model_id
          ? localProxyBase
          : model.model,
      model_lib: model.model_lib.replace(
        "https://raw.githubusercontent.com/mlc-ai/binary-mlc-llm-libs/main/",
        "https://fastly.jsdelivr.net/gh/mlc-ai/binary-mlc-llm-libs@main/",
      ),
    };
  }),
};

function updateNetworkBadge() {
  const badge = $("#networkBadge");
  badge.textContent = navigator.onLine ? "在线：首次可下载模型" : "已离线：使用本机模型";
  badge.classList.toggle("offline", !navigator.onLine);
}

function message(text, type = "assistant") {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${type}`;
  bubble.textContent = text;
  $("#messages").appendChild(bubble);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return bubble;
}

function chineseTokens(text) {
  const english = text.toLowerCase().match(/[a-z]{2,}/g) || [];
  const runs = text.match(/[\u4e00-\u9fff]{2,}/g) || [];
  const chinese = [];
  for (const run of runs) {
    for (const size of [2, 3, 4]) {
      for (let i = 0; i <= run.length - size; i += 1) {
        chinese.push(run.slice(i, i + size));
      }
    }
  }
  return [...new Set([...english, ...chinese])];
}

function rankChunks(question, agent) {
  const tokens = chineseTokens(question);
  const result = [];
  for (const source of COURSE_DATA.sources) {
    if (source.agent !== agent) continue;
    for (const chunk of source.chunks) {
      const text = String(chunk.text || "").toLowerCase();
      const score = tokens.reduce((sum, token) => sum + (text.includes(token) ? token.length : 0), 0);
      if (score) result.push({ source: source.title, text: chunk.text, score });
    }
  }
  return result.sort((a, b) => b.score - a.score).slice(0, 2);
}

function relevantExcerpt(text, tokens, maxLength = 700) {
  const source = String(text || "");
  if (source.length <= maxLength) return source;
  const lowered = source.toLowerCase();
  const positions = tokens
    .map(token => lowered.indexOf(token.toLowerCase()))
    .filter(index => index >= 0);
  const center = positions.length ? Math.min(...positions) : 0;
  const start = Math.max(0, center - 120);
  return source.slice(start, start + maxLength);
}

function renderCourseList() {
  if (!COURSE_DATA) return;
  $("#courseList").innerHTML = Object.entries(agentNames)
    .map(([agent, name]) => {
      const count = COURSE_DATA.sources.filter(source => source.agent === agent).length;
      return `<div class="course"><strong>${name}</strong><span class="tag">${count} 份课程方法</span></div>`;
    })
    .join("");
}

async function loadModel() {
  if (!navigator.gpu) {
    $("#modelStatus").textContent =
      "当前浏览器没有 WebGPU。请使用最新版安卓 Chrome，或在 Chrome 实验设置中启用 WebGPU。";
    return;
  }
  const model = $("#modelSelect").value;
  if (engine && loadedModel === model) {
    message("该模型已经加载，可以直接提问。");
    return;
  }
  modelReady = false;
  $("#askButton").disabled = true;
  $("#askButton").textContent = "请先加载本地模型";
  $("#loadModel").disabled = true;
  $("#modelStatus").textContent = "正在准备模型。首次使用会下载数 GB 文件，请保持屏幕开启和网络连接。";
  try {
    if ("serviceWorker" in navigator && window.isSecureContext) {
      const registration = await navigator.serviceWorker.register("./sw.js");
      await navigator.serviceWorker.ready;
      if (!navigator.serviceWorker.controller) {
        await new Promise(resolve => {
          navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true });
          setTimeout(resolve, 3000);
        });
      }
      registration.active?.postMessage({ type: "ping" });
    }
    if (navigator.storage?.persist && navigator.storage?.estimate) {
      await navigator.storage.persist();
      const estimate = await navigator.storage.estimate();
      $("#modelStatus").textContent =
        `可用存储约 ${Math.round((estimate.quota - estimate.usage) / 1024 ** 3)}GB，开始加载 ${model}。`;
    }
    engine = await webllm.CreateMLCEngine(model, {
      appConfig: offlineAppConfig,
      initProgressCallback: progress => {
        $("#modelStatus").textContent = progress.text;
        const match = progress.text.match(/(\d+(?:\.\d+)?)%/);
        $("#modelProgress").style.width = match ? `${match[1]}%` : "15%";
      },
    }, {
      context_window_size: 2048,
      sliding_window_size: 1024,
    });
    loadedModel = model;
    modelReady = true;
    $("#askButton").disabled = false;
    $("#askButton").textContent = "本地提问";
    $("#modelProgress").style.width = "100%";
    $("#modelStatus").textContent = `${model} 已加载，可断网使用。`;
    message(`本地模型 ${model} 已就绪。`);
  } catch (error) {
    const errorText = String(error.message || error);
    const gpuLost = /GPUBuffer|external Instance|mapAsync|device lost|GPU device/i.test(errorText);
    resetModelState(
      gpuLost
        ? "手机显卡上下文已被系统回收。请改用 Qwen3 1.7B，关闭其他应用并保持本页面在前台，然后重新加载模型。"
        : `模型加载失败：${errorText}`,
    );
    if (gpuLost && $("#modelSelect").value === "Qwen3-4B-q4f16_1-MLC") {
      $("#modelSelect").value = "Qwen3-1.7B-q4f16_1-MLC";
    }
    console.error(error);
  } finally {
    $("#loadModel").disabled = false;
  }
}

function buildPrompt(question, agent) {
  const profile = COURSE_DATA.profiles[agent] || {};
  const chunks = rankChunks(question, agent);
  const tokens = chineseTokens(question);
  const context = chunks.length
    ? chunks
        .map(item => `[${item.source}] ${relevantExcerpt(item.text, tokens)}`)
        .join("\n\n")
        .slice(0, 1800)
    : `课程方法档案：
阅读：${(profile.reading_methods || []).join("；")}
写作：${(profile.writing_methods || []).join("；")}
顺序：${(profile.answer_sequence || []).join(" → ")}`;
  const instructions = `你是私人英语阅读写作助教，不是视频中的老师本人。
只处理${agentNames[agent]}阅读和写作问题。课程资料中的方法可以作为老师课程方法引用，但不能冒充原话。
不要只复述资料：要提炼可迁移方法，并针对新问题做原创推演。阅读回答包含题型判断、证据位置或方法、选项辨析、原创迷你题；写作回答包含结构、稳妥版、进阶版和修改理由。

课程资料与方法：
${context}`;
  return [
    { role: "system", content: instructions },
    { role: "user", content: question },
  ];
}

async function askQuestion(event) {
  event.preventDefault();
  if (!COURSE_DATA) {
    $("#unlockOverlay").hidden = false;
    message("请先输入课程资料密码。", "assistant");
    return;
  }
  const selectedModel = $("#modelSelect").value;
  if (!engine || !modelReady || loadedModel !== selectedModel) {
    message("本地模型尚未加载完成。请先点击“下载并加载模型”，等状态显示“可断网使用”后再提问。", "assistant");
    return;
  }
  const question = $("#question").value.trim();
  if (!question) {
    message("请输入问题或先识别图片。", "assistant");
    return;
  }
  const agent = $("#agent").value;
  message(question, "student");
  $("#question").value = "";
  const waiting = message("本地模型正在思考，手机可能发热并需要一些时间…");
  $("#askButton").disabled = true;
  try {
    const completion = await engine.chat.completions.create({
      messages: buildPrompt(question, agent),
      temperature: 0.45,
      max_tokens: 650,
      enable_thinking: false,
    });
    const content = completion.choices[0]?.message?.content || "模型没有返回内容。";
    waiting.textContent = content.replace(/<think>[\s\S]*?<\/think>/gi, "").trim() || content;
  } catch (error) {
    const errorText = String(error.message || error);
    if (/Model not loaded|reload\(model\)|GPUBuffer|external Instance|mapAsync|device lost|GPU device/i.test(errorText)) {
      const gpuLost = /GPUBuffer|external Instance|mapAsync|device lost|GPU device/i.test(errorText);
      resetModelState(
        gpuLost
          ? "手机的 WebGPU 上下文已经失效。请改用 Qwen3 1.7B，关闭其他应用并重新加载模型。"
          : "模型内存状态已经丢失，请重新加载模型后再提问。",
      );
      if (gpuLost && $("#modelSelect").value === "Qwen3-4B-q4f16_1-MLC") {
        $("#modelSelect").value = "Qwen3-1.7B-q4f16_1-MLC";
      }
      waiting.textContent =
        "本次回答失败：手机的 GPU 会话被系统回收或模型状态丢失。已要求重新加载模型；请优先选择 Qwen3 1.7B，关闭其他占用内存的应用，并保持本页面在前台。";
    } else {
      waiting.textContent = `回答失败：${errorText}`;
    }
  } finally {
    $("#askButton").disabled = false;
  }
}

async function recognizePhoto() {
  if (!selectedPhoto) {
    message("请先拍照或从相册选择图片。");
    return;
  }
  $("#ocrButton").disabled = true;
  $("#ocrButton").textContent = "识别中…";
  try {
    if (!ocrWorker) {
      const base = new URL("./", window.location.href).href;
      ocrWorker = await Tesseract.createWorker(["eng", "chi_sim"], 1, {
        workerPath: new URL("ocr/worker.min.js", base).href,
        corePath: new URL("ocr/tesseract-core-simd-lstm.wasm.js", base).href,
        langPath: new URL("tessdata", base).href.replace(/\/$/, ""),
        logger: status => {
          if (status.status === "recognizing text") {
            $("#ocrButton").textContent = `识别 ${Math.round(status.progress * 100)}%`;
          }
        },
      });
    }
    const result = await ocrWorker.recognize(selectedPhoto);
    $("#question").value = result.data.text.trim();
    message("图片已在本机识别，请检查或修改文字后点击“本地提问”。");
  } catch (error) {
    message(`图片识别失败：${error.message || error}`);
  } finally {
    $("#ocrButton").disabled = false;
    $("#ocrButton").textContent = "识别图片";
  }
}

function setPhoto(file) {
  selectedPhoto = file;
  if (!file) return;
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = URL.createObjectURL(file);
  $("#preview").src = photoUrl;
  $("#preview").classList.add("visible");
}

$("#loadModel").addEventListener("click", loadModel);
$("#modelSelect").addEventListener("change", () => {
  if (loadedModel && $("#modelSelect").value !== loadedModel) {
    modelReady = false;
    $("#askButton").disabled = true;
    $("#askButton").textContent = "请先加载所选模型";
    $("#modelStatus").textContent = "模型已切换，请重新点击“下载并加载模型”。";
  }
});
$("#askForm").addEventListener("submit", askQuestion);
$("#ocrButton").addEventListener("click", recognizePhoto);
$("#cameraInput").addEventListener("change", event => {
  $("#photoInput").value = "";
  setPhoto(event.target.files[0]);
});
$("#photoInput").addEventListener("change", event => {
  $("#cameraInput").value = "";
  setPhoto(event.target.files[0]);
});
window.addEventListener("online", updateNetworkBadge);
window.addEventListener("offline", updateNetworkBadge);

async function tryUnlock() {
  const passphrase = $("#coursePassword").value;
  if (!passphrase) {
    $("#unlockError").textContent = "请输入课程资料密码。";
    return;
  }
  $("#unlockData").disabled = true;
  $("#unlockData").textContent = "正在解密…";
  try {
    await unlockCourseData(passphrase);
  } catch (error) {
    $("#unlockError").textContent = "密码不正确，或课程资料包损坏。";
  } finally {
    $("#unlockData").disabled = false;
    $("#unlockData").textContent = "解锁";
  }
}

$("#unlockData").addEventListener("click", tryUnlock);
$("#coursePassword").addEventListener("keydown", event => {
  if (event.key === "Enter") tryUnlock();
});

const rememberedPassphrase = sessionStorage.getItem("courseDataPassphrase");
if (rememberedPassphrase) {
  unlockCourseData(rememberedPassphrase).catch(() => {
    sessionStorage.removeItem("courseDataPassphrase");
  });
}

renderCourseList();
updateNetworkBadge();
function showUpdateBanner() {
  $("#updateBanner").hidden = false;
}

async function setupServiceWorker() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  const hadController = Boolean(navigator.serviceWorker.controller);
  updateRegistration = await navigator.serviceWorker.register("./sw.js");
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller && !sessionStorage.getItem("swReloaded")) {
    sessionStorage.setItem("swReloaded", "1");
    window.location.reload();
    return;
  }
  if (updateRegistration.waiting) showUpdateBanner();
  updateRegistration.addEventListener("updatefound", () => {
    const worker = updateRegistration.installing;
    worker?.addEventListener("statechange", () => {
      if (worker.state === "installed" && navigator.serviceWorker.controller) {
        showUpdateBanner();
      }
    });
  });
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (pendingUpdateReload) {
      pendingUpdateReload = false;
      window.location.reload();
    } else if (hadController) {
      showUpdateBanner();
    }
  });
  const checkUpdate = () => updateRegistration?.update().catch(() => {});
  window.addEventListener("focus", checkUpdate);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") checkUpdate();
  });
  setInterval(checkUpdate, 5 * 60 * 1000);
  checkUpdate();
}

$("#appVersion").textContent = APP_VERSION;
$("#applyUpdate").addEventListener("click", () => {
  pendingUpdateReload = true;
  const waiting = updateRegistration?.waiting;
  if (waiting) {
    waiting.postMessage({ type: "SKIP_WAITING" });
    setTimeout(() => window.location.reload(), 1500);
  } else {
    window.location.reload();
  }
});
setupServiceWorker().catch(console.error);

window.addEventListener("beforeinstallprompt", event => {
  event.preventDefault();
  installPrompt = event;
});

$("#installOffline").addEventListener("click", async () => {
  if (!installPrompt) {
    message(
      "当前浏览器没有弹出自动安装窗口。请用安卓 Chrome 打开本页，等待约 10–20 秒，然后点击 Chrome 右上角“⋮”，选择“安装应用”或“添加到主屏幕”。",
      "assistant",
    );
    return;
  }
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
});

if (window.matchMedia("(display-mode: standalone)").matches) {
  $("#installOffline").hidden = true;
}
