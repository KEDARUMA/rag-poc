// 定数: 画面が検索入力として受け付ける拡張子。
const ALLOWED_EXTENSIONS = new Set([
  "txt",
  "jpg",
  "jpeg",
  "png",
  "mp4",
  "mov",
  "mkv",
  "avi",
  "webm",
  "pdf",
  "xlsx",
]);
// 状態: 追加順とプレビューURLを保持するドロップファイル一覧。
const fileQueue = [];
// 状態: 新しいファイル項目に割り当てる識別子。
let nextFileId = 1;
// 要素: ファイルドロップ領域。
const dropZone = document.getElementById("drop-zone");
// 要素: ファイル選択用入力。
const fileInput = document.getElementById("file-input");
// 要素: ファイル選択ボタン。
const chooseFilesButton = document.getElementById("choose-files");
// 要素: 追加したファイルのサムネイル一覧。
const fileList = document.getElementById("file-list");
// 要素: テキスト検索入力欄。
const queryText = document.getElementById("query-text");
// 要素: 検索ボタン。
const searchButton = document.getElementById("search-button");
// 要素: 検索状態表示。
const searchStatus = document.getElementById("search-status");
// 要素: 検索結果一覧。
const resultList = document.getElementById("result-list");
// 要素: 検索結果件数。
const resultCount = document.getElementById("result-count");
// 要素: インデックス更新ボタン。
const indexButton = document.getElementById("index-button");
// 要素: インデックス更新の進捗モーダル。
const indexDialog = document.getElementById("index-dialog");
// 要素: 進捗モーダルの閉じるボタン。
const indexDialogClose = document.getElementById("index-dialog-close");
// 要素: インデックス更新の状態表示。
const indexDialogStatus = document.getElementById("index-dialog-status");
// 要素: 処理済み件数表示。
const indexProgressCount = document.getElementById("index-progress-count");
// 要素: 処理済み割合表示。
const indexProgressPercent = document.getElementById("index-progress-percent");
// 要素: インデックス更新の進捗バー。
const indexProgress = document.getElementById("index-progress");
// 要素: 現在処理中のファイル名。
const indexCurrentFile = document.getElementById("index-current-file");
// 要素: インデックス更新ログ一覧。
const indexLog = document.getElementById("index-log");
// 要素: 検索結果プレビュー用モーダル。
const previewDialog = document.getElementById("preview-dialog");
// 要素: プレビューの種別表示。
const previewType = document.getElementById("preview-type");
// 要素: プレビューのファイル名。
const previewTitle = document.getElementById("preview-title");
// 要素: プレビューの検索メタ情報。
const previewMeta = document.getElementById("preview-meta");
// 要素: プレビューの状態表示。
const previewStatus = document.getElementById("preview-status");
// 要素: プレビュー内容の表示領域。
const previewContent = document.getElementById("preview-content");
// 要素: プレビュー元ファイルのダウンロードリンク。
const previewDownload = document.getElementById("preview-download");
// 要素: プレビューモーダルの閉じるボタン。
const previewClose = document.getElementById("preview-close");
// 状態: 検索中かどうか。
let isSearching = false;
// 状態: インデックス更新中かどうか。
let isIndexing = false;
// 状態: 画面へ追加済みの更新ログ件数。
let displayedIndexLogCount = 0;
// 状態: 現在表示している検索結果。
let currentResults = [];
// 状態: 古いテキスト読込応答を無視するための識別子。
let previewRequestId = 0;

/**
 * ファイル名から拡張子を小文字で取り出す。
 */
function getExtension(fileName) {
  const segments = fileName.toLowerCase().split(".");
  return segments.length > 1 ? segments[segments.length - 1] : "";
}

/**
 * 受け付けるファイルを一覧へ追加し、表示を更新する。
 */
function addFiles(files) {
  let rejected = 0;
  for (const file of files) {
    const extension = getExtension(file.name);
    if (!ALLOWED_EXTENSIONS.has(extension)) {
      rejected += 1;
      continue;
    }
    const isPreviewable = ["jpg", "jpeg", "png", "mp4", "mov", "mkv", "avi", "webm"].includes(extension);
    fileQueue.push({
      id: nextFileId,
      file,
      extension,
      previewUrl: isPreviewable ? URL.createObjectURL(file) : "",
    });
    nextFileId += 1;
  }
  renderFiles();
  if (rejected > 0) {
    searchStatus.textContent = "対応していない形式のファイルは追加しませんでした。";
  }
}

/**
 * 現在のファイル項目をサムネイル一覧へ描画する。
 */
function renderFiles() {
  fileList.replaceChildren();
  for (const item of fileQueue) {
    const card = document.createElement("li");
    card.className = "thumbnail-card";
    card.dataset.fileId = String(item.id);

    const removeButton = document.createElement("button");
    removeButton.className = "remove-file";
    removeButton.type = "button";
    removeButton.dataset.removeFileId = String(item.id);
    removeButton.setAttribute("aria-label", item.file.name + "を削除");
    removeButton.textContent = "×";
    card.append(removeButton);

    const visual = document.createElement("div");
    visual.className = "thumbnail-visual";
    if (["jpg", "jpeg", "png"].includes(item.extension)) {
      const image = document.createElement("img");
      image.src = item.previewUrl;
      image.alt = "";
      visual.append(image);
    } else if (["mp4", "mov", "mkv", "avi", "webm"].includes(item.extension)) {
      const video = document.createElement("video");
      video.src = item.previewUrl;
      video.muted = true;
      video.preload = "metadata";
      video.setAttribute("aria-label", item.file.name);
      visual.append(video);
    } else {
      const icon = document.createElement("span");
      icon.className = "thumbnail-icon";
      icon.textContent = item.extension.toUpperCase();
      visual.append(icon);
    }
    card.append(visual);

    const name = document.createElement("p");
    name.className = "thumbnail-name";
    name.title = item.file.name;
    name.textContent = item.file.name;
    card.append(name);
    fileList.append(card);
  }
}

/**
 * 指定したファイルを一覧から除き、プレビュー資源を解放する。
 */
function removeFile(fileId) {
  const itemIndex = fileQueue.findIndex((item) => item.id === fileId);
  if (itemIndex < 0) {
    return;
  }
  const removedItems = fileQueue.splice(itemIndex, 1);
  if (removedItems[0].previewUrl) {
    URL.revokeObjectURL(removedItems[0].previewUrl);
  }
  renderFiles();
}

/**
 * 検索結果を関連情報付きのプレビューカードとして描画する。
 */
function renderResults(results) {
  currentResults = results;
  resultList.replaceChildren();
  resultCount.textContent = results.length + "件";
  if (results.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "一致する結果がありません。";
    resultList.append(empty);
    return;
  }
  for (const [index, result] of results.entries()) {
    // 定数: 1件の検索結果を囲むカード要素。
    const row = document.createElement("li");
    row.className = "result-row";
    // 定数: ファイルプレビューを開くボタン。
    const openButton = document.createElement("button");
    openButton.className = "result-open";
    openButton.type = "button";
    openButton.dataset.resultIndex = String(index);
    openButton.textContent = result.name;
    row.append(openButton);

    // 定数: 類似度とファイル固有情報の表示欄。
    const metadata = document.createElement("p");
    metadata.className = "result-metadata";
    metadata.textContent = formatResultMetadata(result);
    row.append(metadata);
    resultList.append(row);
  }
}

/**
 * 検索結果の種類を画面表示用の日本語へ変換する。
 */
function getResultTypeLabel(type) {
  if (type === "video") {
    return "動画";
  }
  if (type === "image") {
    return "画像";
  }
  if (type === "text") {
    return "テキスト";
  }
  return "ファイル";
}

/**
 * 秒数を時分秒の表示へ変換する。
 */
function formatMediaTime(value) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  // 定数: 表示可能な0以上の秒数。
  const totalSeconds = Number(value);
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) {
    return "";
  }
  // 定数: 秒を切り捨てた整数値。
  const wholeSeconds = Math.floor(totalSeconds);
  // 定数: 時間単位の値。
  const hours = Math.floor(wholeSeconds / 3600);
  // 定数: 時間を除いた分単位の値。
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  // 定数: 分を除いた秒単位の値。
  const seconds = wholeSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * 検索結果に含まれる類似度とメタ情報を整形する。
 */
function formatResultMetadata(result) {
  // 定数: カードへ表示する情報の一覧。
  const details = [getResultTypeLabel(result.type)];
  // 定数: Qdrantが返した類似度スコア。
  const score = Number(result.score);
  if (Number.isFinite(score)) {
    details.push(`類似度 ${score.toFixed(3)}`);
  }
  if (result.type === "video") {
    // 定数: 動画シーンの開始・終了時刻。
    const sceneStart = formatMediaTime(result.start);
    const sceneEnd = formatMediaTime(result.end);
    // 定数: 類似フレームの時刻。
    const hitTime = formatMediaTime(result.time);
    if (sceneStart && sceneEnd) {
      details.push(`シーン ${sceneStart}–${sceneEnd}`);
    }
    if (hitTime) {
      details.push(`該当 ${hitTime}`);
    }
  } else if (result.type === "image" && result.width && result.height) {
    details.push(`${result.width} × ${result.height}px`);
  } else if (result.type === "text" && Number.isInteger(result.chunk_index)) {
    details.push(`チャンク ${result.chunk_index + 1}`);
  }
  return details.join("　・　");
}

/**
 * 選択した検索結果を動画・画像・TXTのモーダルプレビューで開く。
 */
async function openResultPreview(result) {
  // 定数: このプレビュー要求に割り当てる識別子。
  const requestId = previewRequestId + 1;
  previewRequestId = requestId;
  // 定数: ブラウザーから同一オリジンで取得するファイルURL。
  const fileUrl = `/api/file?path=${encodeURIComponent(result.path)}`;
  previewType.textContent = getResultTypeLabel(result.type);
  previewTitle.textContent = result.name;
  previewMeta.textContent = formatResultMetadata(result);
  previewStatus.textContent = "";
  previewContent.replaceChildren();
  previewDownload.href = fileUrl;
  previewDownload.download = result.name;
  previewDownload.hidden = false;
  if (!previewDialog.open) {
    previewDialog.showModal();
  }

  if (result.type === "video") {
    // 定数: 検索結果の動画プレーヤー。
    const video = document.createElement("video");
    // 定数: H.264非対応ブラウザー向けのVP9 WebM配信URL。
    const previewUrl = `${fileUrl}&format=webm`;
    video.className = "preview-video";
    video.controls = true;
    video.preload = "metadata";
    video.src = previewUrl;
    previewStatus.textContent = "ブラウザー用動画を準備しています…";
    // 関数: メタデータ読み込み後に検索で一致した時刻へ移動する。
    video.addEventListener("loadedmetadata", function seekToMatch() {
      if (requestId !== previewRequestId) {
        return;
      }
      // 定数: 検索で一致した動画時刻。
      const hitTime = Number(result.time);
      if (Number.isFinite(hitTime) && hitTime >= 0) {
        video.currentTime = Math.min(hitTime, Math.max(video.duration - 0.05, 0));
      }
    }, { once: true });
    // 関数: 再生可能なデータが届いたら準備中表示を消す。
    video.addEventListener("canplay", function clearVideoStatus() {
      if (requestId === previewRequestId) {
        previewStatus.textContent = "";
      }
    }, { once: true });
    // 関数: 再生できない動画のエラーをモーダルへ表示する。
    video.addEventListener("error", function showVideoError() {
      if (requestId === previewRequestId) {
        previewStatus.textContent = "ブラウザー用動画を読み込めませんでした。ダウンロードして確認してください。";
      }
    }, { once: true });
    previewContent.append(video);
    return;
  }

  if (result.type === "image") {
    // 定数: 検索結果の画像プレビュー。
    const image = document.createElement("img");
    image.className = "preview-image";
    image.src = fileUrl;
    image.alt = result.name;
    // 関数: 読み込めない画像のエラーをモーダルへ表示する。
    image.addEventListener("error", function showImageError() {
      if (requestId === previewRequestId) {
        previewStatus.textContent = "画像を読み込めませんでした。";
      }
    }, { once: true });
    previewContent.append(image);
    return;
  }

  if (result.type === "text") {
    previewStatus.textContent = "読み込み中…";
    try {
      // 定数: TXTファイル取得APIの応答。
      const response = await fetch(fileUrl);
      if (!response.ok) {
        throw new Error("TXTファイルを読み込めませんでした。");
      }
      // 定数: TXTファイル全体の内容。
      const text = await response.text();
      if (requestId !== previewRequestId) {
        return;
      }
      // 定数: TXT全文を表示するテキスト領域。
      const documentText = document.createElement("pre");
      documentText.className = "preview-text";
      documentText.textContent = text;
      previewContent.append(documentText);
      previewStatus.textContent = "";
    } catch (error) {
      if (requestId === previewRequestId) {
        previewStatus.textContent = error instanceof Error
          ? error.message
          : "TXTファイルを読み込めませんでした。";
      }
    }
    return;
  }

  previewStatus.textContent = "このファイル形式はプレビューできません。";
}

/**
 * ファイルカードのクリックを対応するモーダルプレビューへつなぐ。
 */
function handleResultListClick(event) {
  // 定数: クリック対象のプレビューボタン。
  const openButton = event.target.closest("[data-result-index]");
  if (!openButton) {
    return;
  }
  // 定数: ボタンの番号から取得する検索結果。
  const result = currentResults[Number(openButton.dataset.resultIndex)];
  if (result) {
    openResultPreview(result);
  }
}

/**
 * プレビューモーダルを閉じたときに再生と一時表示を片付ける。
 */
function handlePreviewDialogClose() {
  previewRequestId += 1;
  // 定数: モーダル内の動画プレーヤー。
  const video = previewContent.querySelector("video");
  if (video) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  previewContent.replaceChildren();
  previewDownload.removeAttribute("href");
  previewDownload.removeAttribute("download");
  previewDownload.hidden = true;
}

/**
 * プレビューモーダルを閉じる。
 */
function closePreviewDialog() {
  if (previewDialog.open) {
    previewDialog.close();
  }
}

/**
 * 入力テキストと追加ファイルを送り、検索結果を同じ画面に更新する。
 */
async function runSearch() {
  if (previewDialog.open) {
    closePreviewDialog();
  }
  isSearching = true;
  syncActionButtons();
  searchStatus.textContent = "検索しています…";
  resultList.replaceChildren();
  resultCount.textContent = "";

  const requestBody = new FormData();
  requestBody.append("query", queryText.value);
  for (const item of fileQueue) {
    requestBody.append("files", item.file, item.file.name);
  }

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      body: requestBody,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "検索に失敗しました。");
    }
    renderResults(payload.results);
    searchStatus.textContent = "検索が完了しました。";
  } catch (error) {
    const message = error instanceof Error ? error.message : "検索に失敗しました。";
    searchStatus.textContent = message;
    resultList.replaceChildren();
    resultCount.textContent = "";
    const errorRow = document.createElement("li");
    errorRow.className = "empty-state";
    errorRow.textContent = message;
    resultList.append(errorRow);
  } finally {
    isSearching = false;
    syncActionButtons();
  }
}

/**
 * 検索とインデックス更新の実行状態に合わせてボタンを切り替える。
 */
function syncActionButtons() {
  // 定数: 操作を受け付けない共通の実行状態。
  const isBusy = isSearching || isIndexing;
  searchButton.disabled = isBusy;
  indexButton.disabled = isBusy;
}

/**
 * サーバーから届いた更新状態と新しいログをモーダルへ反映する。
 */
function renderIndexStatus(payload) {
  // 定数: サーバーから受け取った処理済み件数。
  const processed = Number(payload.processed) || 0;
  // 定数: 再構築対象となるファイル件数。
  const total = Number(payload.total) || 0;
  // 定数: 更新が終了状態かどうか。
  const isFinished = payload.status !== "running" && payload.status !== "idle";
  // 定数: ファイル完了数から計算する実進捗率。
  const percent = total > 0 ? Math.floor((processed / total) * 100) : isFinished ? 100 : 0;

  indexProgress.value = Math.min(100, percent);
  indexProgressCount.textContent = `${processed} / ${total}件`;
  indexProgressPercent.textContent = `${Math.min(100, percent)}%`;
  indexCurrentFile.textContent = payload.current_file
    ? `処理中: ${payload.current_file}`
    : "";
  isIndexing = payload.status === "running";
  syncActionButtons();

  if (payload.status === "running") {
    indexDialogStatus.textContent = "インデックスを再構築しています…";
  } else if (payload.status === "completed_with_errors") {
    indexDialogStatus.textContent = "更新は完了しました。一部ファイルでエラーが発生しました。";
  } else if (payload.status === "completed") {
    indexDialogStatus.textContent = "インデックスの更新が完了しました。";
  } else if (payload.status === "failed") {
    indexDialogStatus.textContent = payload.error || "インデックスの更新に失敗しました。";
  }

  indexDialogClose.hidden = !isFinished;
  for (const entry of payload.logs.slice(displayedIndexLogCount)) {
    // 定数: 画面に追加するログ項目。
    const logItem = document.createElement("li");
    logItem.dataset.level = entry.level || "info";
    logItem.textContent = entry.message;
    indexLog.append(logItem);
  }
  displayedIndexLogCount = payload.logs.length;
  indexLog.scrollTop = indexLog.scrollHeight;
}

/**
 * 更新状態を取得し、処理中なら次の状態確認を予約する。
 */
async function pollIndexStatus() {
  try {
    // 定数: 更新状態取得APIの応答。
    const response = await fetch("/api/index/status", { cache: "no-store" });
    // 定数: 更新状態とログのJSONデータ。
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "更新状態を取得できませんでした。");
    }
    renderIndexStatus(payload);
    if (payload.status === "running") {
      window.setTimeout(pollIndexStatus, 500);
    }
  } catch (error) {
    // 定数: 進捗状態を取得できないときの表示文。
    const message = error instanceof Error ? error.message : "更新状態を取得できませんでした。";
    indexDialogStatus.textContent = message;
    if (isIndexing) {
      window.setTimeout(pollIndexStatus, 1000);
    }
  }
}

/**
 * 全件再構築を開始し、完了まで進捗状態を取得する。
 */
async function startIndexUpdate() {
  if (isSearching || isIndexing) {
    return;
  }
  if (!indexDialog.open) {
    indexDialog.showModal();
  }
  indexLog.replaceChildren();
  displayedIndexLogCount = 0;
  indexProgress.value = 0;
  indexProgressCount.textContent = "0 / 0件";
  indexProgressPercent.textContent = "0%";
  indexCurrentFile.textContent = "";
  indexDialogStatus.textContent = "インデックス更新を開始しています…";
  indexDialogClose.hidden = true;
  isIndexing = true;
  syncActionButtons();

  try {
    // 定数: 更新開始APIの応答。
    const response = await fetch("/api/index", { method: "POST" });
    // 定数: 更新開始APIのJSONデータ。
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "インデックス更新を開始できませんでした。");
    }
    pollIndexStatus();
  } catch (error) {
    // 定数: 更新開始に失敗したときの表示文。
    const message = error instanceof Error ? error.message : "インデックス更新を開始できませんでした。";
    isIndexing = false;
    syncActionButtons();
    indexDialogStatus.textContent = message;
    indexDialogClose.hidden = false;
    // 定数: モーダルへ追加する開始エラー項目。
    const logItem = document.createElement("li");
    logItem.dataset.level = "error";
    logItem.textContent = message;
    indexLog.append(logItem);
  }
}

/**
 * ページ再読み込み後も実行中の更新状態をモーダルへ復元する。
 */
async function restoreIndexStatus() {
  try {
    // 定数: 復元する更新状態の応答。
    const response = await fetch("/api/index/status", { cache: "no-store" });
    // 定数: 復元する更新状態とログのJSONデータ。
    const payload = await response.json();
    if (!response.ok) {
      return;
    }
    displayedIndexLogCount = 0;
    renderIndexStatus(payload);
    if (payload.status === "running") {
      if (!indexDialog.open) {
        indexDialog.showModal();
      }
      pollIndexStatus();
    }
  } catch {
    return;
  }
}

/**
 * 更新中は進捗モーダルを閉じないようにする。
 */
function handleIndexDialogCancel(event) {
  if (isIndexing) {
    event.preventDefault();
  }
}

/**
 * 更新完了後に進捗モーダルを閉じる。
 */
function closeIndexDialog() {
  indexDialog.close();
}

/**
 * ファイル選択ダイアログで選んだファイルを一覧へ追加する。
 */
function handleFileSelection() {
  addFiles(fileInput.files);
  fileInput.value = "";
}

/**
 * ドロップされたファイルを一覧へ追加する。
 */
function handleDrop(event) {
  event.preventDefault();
  dropZone.classList.remove("is-dragging");
  addFiles(event.dataTransfer.files);
}

/**
 * ファイル一覧内の削除ボタン操作を処理する。
 */
function handleFileListClick(event) {
  const removeButton = event.target.closest("[data-remove-file-id]");
  if (removeButton) {
    removeFile(Number(removeButton.dataset.removeFileId));
  }
}

/**
 * ドロップ中の見た目を更新する。
 */
function handleDragOver(event) {
  event.preventDefault();
  dropZone.classList.add("is-dragging");
}

/**
 * ファイルが領域から離れたときにドロップ中の見た目を戻す。
 */
function handleDragLeave(event) {
  if (!dropZone.contains(event.relatedTarget)) {
    dropZone.classList.remove("is-dragging");
  }
}

chooseFilesButton.addEventListener("click", function openFilePicker() {
  fileInput.click();
});
fileInput.addEventListener("change", handleFileSelection);
dropZone.addEventListener("dragover", handleDragOver);
dropZone.addEventListener("dragleave", handleDragLeave);
dropZone.addEventListener("drop", handleDrop);
fileList.addEventListener("click", handleFileListClick);
searchButton.addEventListener("click", runSearch);
resultList.addEventListener("click", handleResultListClick);
previewClose.addEventListener("click", closePreviewDialog);
previewDialog.addEventListener("close", handlePreviewDialogClose);
indexButton.addEventListener("click", startIndexUpdate);
indexDialogClose.addEventListener("click", closeIndexDialog);
indexDialog.addEventListener("cancel", handleIndexDialogCancel);
restoreIndexStatus();
