#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path.cwd()
AUDIO_PY = ROOT / "backend/open_webui/routers/audio.py"
AUDIO_API_TS = ROOT / "src/lib/apis/audio/index.ts"
RESPONSE_SVELTE = ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
PCM_AUDIO_TS = ROOT / "src/lib/utils/pcmStreamingAudio.ts"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def read(path: Path) -> str:
    if not path.exists():
        fail(f"找不到文件: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"updated: {path.relative_to(ROOT)}")


def replace_regex_once(text: str, pattern: str, repl, desc: str, flags: int = re.DOTALL) -> str:
    new_text, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count == 0:
        fail(f"无法定位代码片段: {desc}")
    print(f"patched: {desc}")
    return new_text


def patch_backend_audio_py() -> None:
    text = read(AUDIO_PY)

    if "StreamingResponse" not in re.search(r"from fastapi\.responses import[^\n]+", text).group(0):
        text = text.replace(
            "from fastapi.responses import FileResponse",
            "from fastapi.responses import FileResponse, StreamingResponse",
            1,
        )
        print("patched: audio.py 导入 StreamingResponse")
    else:
        print("skip: audio.py 已导入 StreamingResponse")

    if "requested_tts_format" not in text:
        def add_stream_flags(match: re.Match) -> str:
            indent = match.group("indent")
            return (
                f"{indent}body = await request.body()\n"
                f"{indent}body_compact = body.replace(b' ', b'').replace(b'\\n', b'').replace(b'\\t', b'').lower()\n"
                f"{indent}stream_tts = (\n"
                f"{indent}    b'\\\"stream\\\":true' in body_compact\n"
                f"{indent}    or request.headers.get('X-OpenWebUI-TTS-Stream') == '1'\n"
                f"{indent})\n"
                f"{indent}requested_tts_format = str(\n"
                f"{indent}    request.headers.get('X-OpenWebUI-TTS-Format') or ''\n"
                f"{indent}).lower().strip()\n"
                f"{indent}name = hashlib.sha256("
            )

        text = replace_regex_once(
            text,
            r"(?P<indent>[ \t]+)body = await request\.body\(\)\n(?P=indent)name = hashlib\.sha256\(",
            add_stream_flags,
            "speech() 中加入 stream/format 开关",
        )
    else:
        print("skip: audio.py 已有 requested_tts_format")

    if "if file_path.is_file() and not stream_tts" not in text:
        text = replace_regex_once(
            text,
            r"([ \t]+)# Check if the file already exists in the cache\n([ \t]+)if file_path\.is_file\(\):\n([ \t]+)return FileResponse\(file_path\)",
            lambda m: f"{m.group(1)}# Check if the file already exists in the cache\n{m.group(2)}if file_path.is_file() and not stream_tts:\n{m.group(3)}return FileResponse(file_path)",
            "stream 模式跳过 speech mp3 缓存",
        )
    else:
        print("skip: audio.py 已跳过 stream 缓存")

    if "payload.get('stream', stream_tts)" not in text:
        text = replace_regex_once(
            text,
            r"([ \t]+)payload = json\.loads\(body\.decode\('utf-8'\)\)",
            lambda m: (
                f"{m.group(1)}payload = json.loads(body.decode('utf-8'))\n"
                f"{m.group(1)}stream_tts = bool(payload.get('stream', stream_tts))\n"
                f"{m.group(1)}requested_tts_format = str(\n"
                f"{m.group(1)}    payload.get('response_format')\n"
                f"{m.group(1)}    or request.headers.get('X-OpenWebUI-TTS-Format')\n"
                f"{m.group(1)}    or requested_tts_format\n"
                f"{m.group(1)}).lower().strip()"
            ),
            "从 JSON payload 读取 stream/response_format",
        )
    else:
        print("skip: audio.py 已从 payload 读取 stream")

    if "payload['stream'] = True" not in text:
        text = replace_regex_once(
            text,
            r"([ \t]+)payload\['model'\] = request\.app\.state\.config\.TTS_MODEL",
            lambda m: (
                f"{m.group(1)}payload['model'] = request.app.state.config.TTS_MODEL\n"
                f"{m.group(1)}if stream_tts:\n"
                f"{m.group(1)}    payload['stream'] = True\n"
                f"{m.group(1)}    if requested_tts_format:\n"
                f"{m.group(1)}        payload['response_format'] = requested_tts_format"
            ),
            "OpenAI-compatible TTS payload 加入 stream/response_format",
        )
    else:
        print("skip: audio.py 已设置 payload stream")

    def build_streaming_branch(indent: str) -> str:
        inner = indent + "    "
        inner2 = inner + "    "
        inner3 = inner2 + "    "
        return (
            f"{indent}if stream_tts:\n"
            f"{inner}stream_timeout = aiohttp.ClientTimeout(total=None)\n"
            f"{inner}stream_session = aiohttp.ClientSession(timeout=stream_timeout, trust_env=True)\n"
            f"{inner}r = await stream_session.post(\n"
            f"{inner2}url=f'{{request.app.state.config.TTS_OPENAI_API_BASE_URL}}/audio/speech',\n"
            f"{inner2}json=payload,\n"
            f"{inner2}headers=headers,\n"
            f"{inner2}ssl=AIOHTTP_CLIENT_SESSION_SSL,\n"
            f"{inner})\n"
            f"{inner}try:\n"
            f"{inner2}r.raise_for_status()\n"
            f"{inner}except Exception:\n"
            f"{inner2}await stream_session.close()\n"
            f"{inner2}raise\n\n"
            f"{inner}content_type_header = r.headers.get('Content-Type', 'application/octet-stream')\n"
            f"{inner}response_headers = {{\n"
            f"{inner2}'Cache-Control': 'no-store',\n"
            f"{inner2}'X-Accel-Buffering': 'no',\n"
            f"{inner}}}\n"
            f"{inner}for header_name in (\n"
            f"{inner2}'X-Audio-Codec',\n"
            f"{inner2}'X-Audio-Sample-Rate',\n"
            f"{inner2}'X-Audio-Channels',\n"
            f"{inner2}'X-Audio-Endian',\n"
            f"{inner}):\n"
            f"{inner2}header_value = r.headers.get(header_name)\n"
            f"{inner2}if header_value:\n"
            f"{inner3}response_headers[header_name] = header_value\n\n"
            f"{inner}async def iter_tts_audio():\n"
            f"{inner2}try:\n"
            f"{inner3}async for chunk in r.content.iter_chunked(16384):\n"
            f"{inner3}    if chunk:\n"
            f"{inner3}        yield chunk\n"
            f"{inner2}finally:\n"
            f"{inner3}r.release()\n"
            f"{inner3}await stream_session.close()\n\n"
            f"{inner}return StreamingResponse(\n"
            f"{inner2}iter_tts_audio(),\n"
            f"{inner2}media_type=content_type_header,\n"
            f"{inner2}headers=response_headers,\n"
            f"{inner})\n\n"
        )

    if "async def iter_tts_audio()" not in text:
        marker_pos = text.find("headers = include_user_info_headers(headers, user)")
        if marker_pos < 0:
            fail("无法定位 include_user_info_headers，不能插入流式代理分支")
        after_marker = text[marker_pos:]

        old_branch_match = re.search(
            r"(?P<indent>[ \t]+)if stream_tts:\n[\s\S]*?\n(?P=indent)r = await session\.post\(",
            after_marker,
        )

        if old_branch_match and "async def iter_audio()" in old_branch_match.group(0):
            indent = old_branch_match.group("indent")
            replacement = build_streaming_branch(indent) + f"{indent}r = await session.post("
            local_start = old_branch_match.start()
            local_end = old_branch_match.end()
            after_marker = after_marker[:local_start] + replacement + after_marker[local_end:]
            text = text[:marker_pos] + after_marker
            print("patched: 替换旧 MP3 streaming 分支为 PCM 透传分支")
        else:
            pattern = (
                r"(?P<prefix>(?P<indent>[ \t]+)if ENABLE_FORWARD_USER_INFO_HEADERS:\n"
                r"(?P<include>[\s\S]*?include_user_info_headers\(headers, user\)\n\s*\n))"
                r"(?P=indent)r = await session\.post\("
            )

            def add_streaming_branch(match: re.Match) -> str:
                indent = match.group("indent")
                return match.group("prefix") + build_streaming_branch(indent) + f"{indent}r = await session.post("

            text = replace_regex_once(
                text,
                pattern,
                add_streaming_branch,
                "OpenAI-compatible TTS 加入 PCM 流式代理分支",
            )
    else:
        print("skip: audio.py 已有 iter_tts_audio")

    write(AUDIO_PY, text)


def replace_synthesize_function() -> None:
    text = read(AUDIO_API_TS)
    start = text.find("export const synthesizeOpenAISpeech = async (")
    if start < 0:
        fail("无法找到 synthesizeOpenAISpeech")
    end = text.find("\n};", start)
    if end < 0:
        fail("无法找到 synthesizeOpenAISpeech 结束位置")
    end += len("\n};")

    new_func = """export const synthesizeOpenAISpeech = async (
\ttoken: string = '',
\tspeaker: string = 'alloy',
\ttext: string = '',
\tmodel?: string,
\toptions?: { stream?: boolean; format?: 'pcm' | 'mp3'; signal?: AbortSignal }
) => {
\tlet error = null;
\tconst format = options?.format ?? (options?.stream ? 'pcm' : undefined);

\tconst res = await fetch(`${AUDIO_API_BASE_URL}/speech`, {
\t\tmethod: 'POST',
\t\tsignal: options?.signal,
\t\theaders: {
\t\t\tAuthorization: `Bearer ${token}`,
\t\t\t'Content-Type': 'application/json',
\t\t\t...(options?.stream && {
\t\t\t\tAccept: format === 'pcm' ? 'application/octet-stream' : 'audio/mpeg',
\t\t\t\t'X-OpenWebUI-TTS-Stream': '1',
\t\t\t\t...(format && { 'X-OpenWebUI-TTS-Format': format })
\t\t\t})
\t\t},
\t\tbody: JSON.stringify({
\t\t\tinput: text,
\t\t\tvoice: speaker,
\t\t\t...(model && { model }),
\t\t\t...(options?.stream && { stream: true }),
\t\t\t...(format && { response_format: format })
\t\t})
\t})
\t\t.then(async (res) => {
\t\t\tif (!res.ok) throw await res.json();
\t\t\treturn res;
\t\t})
\t\t.catch((err) => {
\t\t\terror = err.detail;
\t\t\tconsole.error(err);
\t\t\treturn null;
\t\t});

\tif (error) {
\t\tthrow error;
\t}

\treturn res;
};"""

    text = text[:start] + new_func + text[end:]
    print("patched: src/lib/apis/audio/index.ts 替换 synthesizeOpenAISpeech")
    write(AUDIO_API_TS, text)


PCM_HELPER_TS = r"""type PcmStreamOptions = {
	signal?: AbortSignal;
	initialBufferSeconds?: number;
	minChunkSeconds?: number;
	onPlaybackStart?: () => void;
};

const parsePositiveIntegerHeader = (res: Response, name: string, fallback: number): number => {
	const value = Number.parseInt(res.headers.get(name) ?? '', 10);
	return Number.isFinite(value) && value > 0 ? value : fallback;
};

const concatBytes = (left: Uint8Array, right: Uint8Array): Uint8Array => {
	if (!left.length) return right;
	if (!right.length) return left;
	const out = new Uint8Array(left.length + right.length);
	out.set(left, 0);
	out.set(right, left.length);
	return out;
};

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export const isPcmStreamResponse = (res: Response): boolean => {
	const codec = (res.headers.get('X-Audio-Codec') ?? '').toLowerCase();
	const contentType = (res.headers.get('Content-Type') ?? '').toLowerCase();

	return (
		codec.includes('pcm') ||
		codec.includes('s16le') ||
		contentType.includes('audio/pcm') ||
		contentType.includes('audio/l16') ||
		contentType.includes('application/octet-stream')
	);
};

export const playPcmStreamResponse = async (
	res: Response,
	{
		signal,
		initialBufferSeconds = 0.35,
		minChunkSeconds = 0.12,
		onPlaybackStart
	}: PcmStreamOptions = {}
): Promise<void> => {
	if (!res.body) {
		throw new Error('TTS stream response has no body');
	}

	const AudioContextImpl = window.AudioContext ?? window.webkitAudioContext;
	if (!AudioContextImpl) {
		throw new Error('This browser does not support Web Audio API');
	}

	const sampleRate = parsePositiveIntegerHeader(res, 'X-Audio-Sample-Rate', 48000);
	const channels = parsePositiveIntegerHeader(res, 'X-Audio-Channels', 1);
	const bytesPerSample = 2;
	const frameBytes = channels * bytesPerSample;
	const minChunkBytes = Math.max(
		frameBytes,
		Math.floor((sampleRate * channels * bytesPerSample * minChunkSeconds) / frameBytes) * frameBytes
	);

	const reader = res.body.getReader();
	const audioContext = new AudioContextImpl();
	await audioContext.resume();

	let pending = new Uint8Array(0);
	let nextPlayTime = audioContext.currentTime + initialBufferSeconds;
	let scheduledSources: AudioBufferSourceNode[] = [];
	let firstChunkScheduled = false;
	let aborted = false;

	const cleanupScheduledSource = (source: AudioBufferSourceNode) => {
		scheduledSources = scheduledSources.filter((item) => item !== source);
	};

	const stopAll = async () => {
		aborted = true;
		for (const source of scheduledSources) {
			try {
				source.stop();
			} catch {}
		}
		scheduledSources = [];
		try {
			await reader.cancel();
		} catch {}
		try {
			await audioContext.close();
		} catch {}
	};

	const abortHandler = () => {
		void stopAll();
	};

	if (signal?.aborted) {
		await stopAll();
		return;
	}

	signal?.addEventListener('abort', abortHandler, { once: true });

	const schedulePcm = (bytes: Uint8Array) => {
		if (aborted || !bytes.length) return;

		const frames = Math.floor(bytes.length / frameBytes);
		if (frames <= 0) return;

		const audioBuffer = audioContext.createBuffer(channels, frames, sampleRate);
		const dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

		for (let frame = 0; frame < frames; frame += 1) {
			for (let channel = 0; channel < channels; channel += 1) {
				const sampleOffset = (frame * channels + channel) * bytesPerSample;
				const sample = dataView.getInt16(sampleOffset, true) / 32768;
				audioBuffer.getChannelData(channel)[frame] = sample;
			}
		}

		const source = audioContext.createBufferSource();
		source.buffer = audioBuffer;
		source.connect(audioContext.destination);
		source.onended = () => cleanupScheduledSource(source);

		// If generation stalls, keep playback moving instead of scheduling into the past.
		nextPlayTime = Math.max(nextPlayTime, audioContext.currentTime + 0.03);
		source.start(nextPlayTime);
		scheduledSources.push(source);

		if (!firstChunkScheduled) {
			firstChunkScheduled = true;
			onPlaybackStart?.();
		}

		nextPlayTime += audioBuffer.duration;
	};

	const flushPending = (force = false) => {
		const usableBytes = Math.floor(pending.length / frameBytes) * frameBytes;
		if (usableBytes <= 0) return;
		if (!force && usableBytes < minChunkBytes) return;

		const chunk = pending.slice(0, usableBytes);
		pending = pending.slice(usableBytes);
		schedulePcm(chunk);
	};

	try {
		while (true) {
			if (aborted || signal?.aborted) return;

			const { done, value } = await reader.read();
			if (done) break;
			if (!value?.length) continue;

			pending = concatBytes(pending, value);
			flushPending(false);
		}

		flushPending(true);

		if (!firstChunkScheduled) return;

		const remainingMs = Math.max(0, (nextPlayTime - audioContext.currentTime) * 1000) + 80;
		await sleep(remainingMs);
	} finally {
		signal?.removeEventListener('abort', abortHandler);
		if (!aborted) {
			try {
				await audioContext.close();
			} catch {}
		}
	}
};
"""


def write_pcm_helper() -> None:
    write(PCM_AUDIO_TS, PCM_HELPER_TS)


def patch_response_message() -> None:
    text = read(RESPONSE_SVELTE)

    if "$lib/utils/pcmStreamingAudio" not in text:
        text = replace_regex_once(
            text,
            r"(import \{ synthesizeOpenAISpeech \} from '\$lib/apis/audio';\n)",
            r"\1\timport { isPcmStreamResponse, playPcmStreamResponse } from '$lib/utils/pcmStreamingAudio';\n",
            "ResponseMessage.svelte 导入 PCM 播放工具",
            flags=0,
        )
    else:
        print("skip: ResponseMessage.svelte 已导入 PCM 播放工具")

    # Change the second TTS loop to expose idx so we can clear speaking state after the last PCM sentence.
    second_loop = re.search(
        r"(\} else \{\s*\n\s*)for \(const \[, sentence\] of messageContentParts\.entries\(\)\) \{",
        text,
        re.DOTALL,
    )
    if second_loop and "for (const [idx, sentence] of messageContentParts.entries())" not in text[second_loop.start(): second_loop.start() + 400]:
        text = text[: second_loop.start()] + re.sub(
            r"for \(const \[, sentence\] of messageContentParts\.entries\(\)\) \{",
            "for (const [idx, sentence] of messageContentParts.entries()) {",
            text[second_loop.start():],
            count=1,
        )
        print("patched: ResponseMessage.svelte TTS 循环加入 idx")
    else:
        print("skip: ResponseMessage.svelte TTS 循环 idx 已存在或未匹配")

    if "format: 'pcm'" not in text:
        text = replace_regex_once(
            text,
            r"synthesizeOpenAISpeech\(localStorage\.token, voiceId, sentence\)(\.catch\()",
            (
                "synthesizeOpenAISpeech(localStorage.token, voiceId, sentence, undefined, {\n"
                "\t\t\t\t\tstream: true,\n"
                "\t\t\t\t\tformat: 'pcm',\n"
                "\t\t\t\t\tsignal\n"
                "\t\t\t\t})\\1"
            ),
            "ResponseMessage.svelte 请求 PCM 流式 TTS",
        )
    else:
        print("skip: ResponseMessage.svelte 已请求 format pcm")

    pcm_block = """if (isPcmStreamResponse(res)) {
					loadingSpeech = false;
					await playPcmStreamResponse(res, {
						signal,
						initialBufferSeconds: 0.35,
						minChunkSeconds: 0.12
					});

					if (idx === messageContentParts.length - 1 && speaking) {
						speaking = false;
						speakingIdx = undefined;

						if ($settings.conversationMode) {
							document.getElementById('voice-input-button')?.click();
						}
					}
				} else {
					const blob = await res.blob();
					const url = URL.createObjectURL(blob);
					$audioQueue.enqueue(url);
					loadingSpeech = false;
				}"""

    if "playPcmStreamResponse(res" not in text:
        text = replace_regex_once(
            text,
            r"const blob = await res\.blob\(\);\s*\n\s*const url = URL\.createObjectURL\(blob\);\s*\n\s*\$audioQueue\.enqueue\(url\);\s*\n\s*loadingSpeech = false;",
            pcm_block,
            "ResponseMessage.svelte 使用 Web Audio 播放 PCM 流，fallback 到 Blob 队列",
        )
    else:
        print("skip: ResponseMessage.svelte 已使用 playPcmStreamResponse")

    write(RESPONSE_SVELTE, text)


def main() -> None:
    patch_backend_audio_py()
    replace_synthesize_function()
    write_pcm_helper()
    patch_response_message()
    print("\n完成。建议继续运行：")
    print("  git diff --check")
    print("  npm run format")
    print("  npm run build")


if __name__ == "__main__":
    main()
