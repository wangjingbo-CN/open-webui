type PcmStreamOptions = {
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
		initialBufferSeconds = 0.18,
		minChunkSeconds = 0.08,
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
		// nextPlayTime = Math.max(nextPlayTime, audioContext.currentTime + 0.03);
		const now = audioContext.currentTime;

		if (nextPlayTime < now + 0.01) {
			nextPlayTime = now + 0.08;
		}
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
