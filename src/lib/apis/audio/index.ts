import { AUDIO_API_BASE_URL } from '$lib/constants';

export const getAudioConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${AUDIO_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getTTSVoices = async (token: string) => {
	const res = await fetch(`/api/v1/audio/voices`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		throw await res.json().catch(() => ({ detail: res.statusText }));
	}

	return await res.json();
};

type OpenAIConfigForm = {
	url: string;
	key: string;
	model: string;
	speaker: string;
};

export const updateAudioConfig = async (token: string, payload: OpenAIConfigForm) => {
	let error = null;

	const res = await fetch(`${AUDIO_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...payload
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const transcribeAudio = async (token: string, file: File, language?: string) => {
	const data = new FormData();
	data.append('file', file);
	if (language) {
		data.append('language', language);
	}

	let error = null;
	const res = await fetch(`${AUDIO_API_BASE_URL}/transcriptions`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		},
		body: data
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const synthesizeOpenAISpeech = async (
	token: string = '',
	speaker: string = 'alloy',
	text: string = '',
	model?: string
) => {
	let error = null;

	const res = await fetch(`${AUDIO_API_BASE_URL}/speech`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			input: text,
			voice: speaker,
			...(model && { model })
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res;
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);

			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const synthesizeOpenAISpeechStream = async (
	token: string = '',
	speaker: string = 'alloy',
	text: string = '',
	model?: string,
	signal?: AbortSignal
) => {
	let error = null;

	const res = await fetch(`${AUDIO_API_BASE_URL}/speech/stream`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			input: text,
			voice: speaker,
			stream: true,
			response_format: 'pcm',
			pcm_sample_rate: 48000,
			pcm_channels: 2,
			...(model && { model })
		}),
		signal
	})
		.then(async (res) => {
			if (!res.ok) {
				const contentType = res.headers.get('content-type') ?? '';
				throw contentType.includes('application/json') ? await res.json() : await res.text();
			}
			return res;
		})
		.catch((err) => {
			if (err?.name === 'AbortError') {
				throw err;
			}

			error = err?.detail ?? err?.message ?? `${err}`;
			console.error(err);

			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

const concatUint8Arrays = (a: Uint8Array, b: Uint8Array) => {
	const c = new Uint8Array(a.length + b.length);
	c.set(a, 0);
	c.set(b, a.length);
	return c;
};

const pcm16leToAudioBuffer = (
	ctx: AudioContext,
	bytes: Uint8Array,
	sampleRate: number,
	channels: number
) => {
	const frameBytes = channels * 2;
	const frames = Math.floor(bytes.byteLength / frameBytes);
	const buffer = ctx.createBuffer(channels, frames, sampleRate);
	const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

	for (let ch = 0; ch < channels; ch++) {
		const out = buffer.getChannelData(ch);

		for (let i = 0; i < frames; i++) {
			const offset = (i * channels + ch) * 2;
			out[i] = view.getInt16(offset, true) / 32768;
		}
	}

	return buffer;
};

export const playPcm16SpeechStream = async (
	res: Response,
	options: {
		sampleRate?: number;
		channels?: number;
		signal?: AbortSignal;
		initialBufferMs?: number;
		minBufferMs?: number;
		playbackRate?: number;
	} = {}
) => {
	if (!res.body) {
		throw new Error('No response body for PCM stream');
	}

	const sampleRate = Number(res.headers.get('x-audio-sample-rate') || options.sampleRate || 48000);
	const channels = Number(res.headers.get('x-audio-channels') || options.channels || 2);

	const bytesPerSample = 2;
	const bytesPerFrame = channels * bytesPerSample;

	const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
	const audioContext: AudioContext = new AudioContextClass();

	if (audioContext.state === 'suspended') {
		await audioContext.resume();
	}

	const reader = res.body.getReader();

	let pending = new Uint8Array(0);
	let nextStartTime = audioContext.currentTime + (options.initialBufferMs ?? 200) / 1000;
	let stopped = false;

	const sources = new Set<AudioBufferSourceNode>();

	const stopAll = async () => {
		if (stopped) return;
		stopped = true;

		try {
			await reader.cancel();
		} catch {
			// ignore
		}

		for (const source of Array.from(sources)) {
			try {
				source.stop(0);
			} catch {
				// source may already be stopped
			}

			try {
				source.disconnect();
			} catch {
				// ignore
			}
		}

		sources.clear();

		try {
			if (audioContext.state !== 'closed') {
				await audioContext.close();
			}
		} catch {
			// ignore
		}
	};

	const onAbort = () => {
		void stopAll();
	};

	if (options.signal) {
		if (options.signal.aborted) {
			await stopAll();
			return;
		}

		options.signal.addEventListener('abort', onAbort, { once: true });
	}

	const appendBytes = (a: Uint8Array, b: Uint8Array) => {
		if (a.length === 0) return b;
		if (b.length === 0) return a;

		const out = new Uint8Array(a.length + b.length);
		out.set(a, 0);
		out.set(b, a.length);
		return out;
	};

	const schedulePcm = (pcmBytes: Uint8Array) => {
		if (stopped) return;

		const frameCount = Math.floor(pcmBytes.byteLength / bytesPerFrame);
		if (frameCount <= 0) return;

		const view = new DataView(pcmBytes.buffer, pcmBytes.byteOffset, pcmBytes.byteLength);
		const audioBuffer = audioContext.createBuffer(channels, frameCount, sampleRate);

		for (let frame = 0; frame < frameCount; frame++) {
			for (let ch = 0; ch < channels; ch++) {
				const byteOffset = (frame * channels + ch) * 2;
				const sample = view.getInt16(byteOffset, true);
				audioBuffer.getChannelData(ch)[frame] = sample / 32768;
			}
		}

		const source = audioContext.createBufferSource();
		source.buffer = audioBuffer;
		source.playbackRate.value = options.playbackRate ?? 1;
		source.connect(audioContext.destination);

		source.onended = () => {
			sources.delete(source);
			try {
				source.disconnect();
			} catch {
				// ignore
			}
		};

		sources.add(source);

		if (nextStartTime < audioContext.currentTime + 0.02) {
			nextStartTime = audioContext.currentTime + 0.02;
		}

		source.start(nextStartTime);
		nextStartTime += audioBuffer.duration;
	};

	const minFramesPerBuffer = Math.floor(sampleRate * ((options.minBufferMs ?? 160) / 1000));

	const flush = (force = false) => {
		if (stopped) return;

		const availableFrames = Math.floor(pending.byteLength / bytesPerFrame);
		if (availableFrames <= 0) return;
		if (!force && availableFrames < minFramesPerBuffer) return;

		const bytesToConsume = availableFrames * bytesPerFrame;
		const playable = pending.slice(0, bytesToConsume);
		pending = pending.slice(bytesToConsume);

		schedulePcm(playable);
	};

	try {
		while (!stopped) {
			const { done, value } = await reader.read();

			if (stopped) break;

			if (done) {
				flush(true);
				break;
			}

			if (!value || value.byteLength === 0) continue;

			pending = appendBytes(pending, value);
			flush(false);
		}

		if (!stopped) {
			const remainingMs = Math.max(0, nextStartTime - audioContext.currentTime) * 1000;
			await new Promise((resolve) => setTimeout(resolve, remainingMs + 80));
		}
	} catch (err: any) {
		if (err?.name !== 'AbortError' && !stopped) {
			throw err;
		}
	} finally {
		if (options.signal) {
			options.signal.removeEventListener('abort', onAbort);
		}

		await stopAll();
	}
};


interface AvailableModelsResponse {
	models: { name: string; id: string }[] | { id: string }[];
}

export const getModels = async (token: string = ''): Promise<AvailableModelsResponse> => {
	let error = null;

	const res = await fetch(`${AUDIO_API_BASE_URL}/models`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);

			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getVoices = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${AUDIO_API_BASE_URL}/voices`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);

			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
