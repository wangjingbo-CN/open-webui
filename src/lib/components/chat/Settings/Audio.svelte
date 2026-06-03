<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onMount, getContext } from 'svelte';

	import { user, settings, config } from '$lib/stores';
	import { getAudioConfig, getVoices as _getVoices } from '$lib/apis/audio';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	const dispatch = createEventDispatcher();

	const i18n = getContext('i18n');

	export let saveSettings: Function;

	// Audio
	let conversationMode = false;
	let speechAutoSend = false;
	let responseAutoPlayback = false;
	let nonLocalVoices = false;

	let STTEngine = '';
	let STTLanguage = '';

	let TTSEngine = '';
	let TTSEngineConfig = {};

	let TTSModel = null;
	let TTSModelProgress = null;
	let TTSModelLoading = false;

	let voices = [];
	let voice = '';


	const MOSS_DEFAULT_VOICES = [
		{ id: 'default', name: 'Default', localService: true },
		{ id: 'Junhao', name: 'Junhao', description: 'Chinese male voice A', localService: true },
		{ id: 'Zhiming', name: 'Zhiming', description: 'Chinese male voice B', localService: true },
		{ id: 'Weiguo', name: 'Weiguo', description: 'Chinese male voice C', localService: true },
		{ id: 'Xiaoyu', name: 'Xiaoyu', description: 'Chinese female voice A', localService: true },
		{ id: 'Yuewen', name: 'Yuewen', description: 'Chinese female voice B', localService: true },
		{ id: 'Lingyu', name: 'Lingyu', description: 'Chinese female voice C', localService: true },
		{ id: 'Trump', name: 'Trump', description: 'Trump reference voice', localService: true },
		{ id: 'Ava', name: 'Ava', description: 'English female voice A', localService: true },
		{ id: 'Bella', name: 'Bella', description: 'English female voice B', localService: true },
		{ id: 'Adam', name: 'Adam', description: 'English male voice A', localService: true },
		{ id: 'Nathan', name: 'Nathan', description: 'English male voice B', localService: true },
		{ id: 'Yui', name: 'Yui', description: 'Japanese female voice A', localService: true },
	];

	const normalizeVoiceOption = (item) => {
		if (typeof item === 'string') {
			return { id: item, name: item, localService: true };
		}

		const id = String(item?.id ?? item?.voice ?? item?.name ?? '').trim();
		const name = String(item?.name ?? item?.voice ?? item?.id ?? id).trim();

		if (!id) {
			return null;
		}

		return {
			...item,
			id,
			name,
			localService: item?.localService ?? true
		};
	};

	const getMossDefaultVoices = () => MOSS_DEFAULT_VOICES.map((item) => ({ ...item }));

	const isMossTTSModel = async () => {
		const audioConfig = await getAudioConfig(localStorage.token).catch(() => null);
		const model = String(
			audioConfig?.tts?.MODEL ??
				audioConfig?.tts?.model ??
				$config?.audio?.tts?.model ??
				$config?.audio?.tts?.MODEL ??
				''
		).toLowerCase();

		return model.includes('moss') || model.includes('nano');
	};

	// Audio speed control
	let playbackRate = 1;

	const getVoices = async () => {
		if (TTSEngine === 'browser-kokoro') {
			if (!TTSModel) {
				await loadKokoro();
			}

			voices = Object.entries(TTSModel.voices).map(([key, value]) => {
				return {
					id: key,
					name: value.name,
					localService: false
				};
			});
		} else {
			if ($config.audio.tts.engine === '') {
				const getVoicesLoop = setInterval(async () => {
					voices = await speechSynthesis.getVoices();

					// do your loop
					if (voices.length > 0) {
						clearInterval(getVoicesLoop);
					}
				}, 100);
			} else {
				const isMoss = await isMossTTSModel();
				const res = await _getVoices(localStorage.token).catch((e) => {
					if (!isMoss) {
						toast.error(`${e}`);
					}
				});

				if (res) {
					console.log(res);
					const voiceItems = Array.isArray(res) ? res : res.voices ?? res.data ?? [];
					voices = voiceItems.map(normalizeVoiceOption).filter(Boolean);
				}

				if (isMoss && voices.length === 0) {
					voices = getMossDefaultVoices();
				}
			}
		}
	};

	const toggleResponseAutoPlayback = async () => {
		responseAutoPlayback = !responseAutoPlayback;
		saveSettings({ responseAutoPlayback: responseAutoPlayback });
	};

	const toggleSpeechAutoSend = async () => {
		speechAutoSend = !speechAutoSend;
		saveSettings({ speechAutoSend: speechAutoSend });
	};

	onMount(async () => {
		playbackRate = $settings.audio?.tts?.playbackRate ?? 1;
		conversationMode = $settings.conversationMode ?? false;
		speechAutoSend = $settings.speechAutoSend ?? false;
		responseAutoPlayback = $settings.responseAutoPlayback ?? false;

		STTEngine = $settings?.audio?.stt?.engine ?? '';
		STTLanguage = $settings?.audio?.stt?.language ?? '';

		TTSEngine = $settings?.audio?.tts?.engine ?? '';
		TTSEngineConfig = $settings?.audio?.tts?.engineConfig ?? {};

		if ($settings?.audio?.tts?.defaultVoice === $config.audio.tts.voice) {
			voice = $settings?.audio?.tts?.voice ?? $config.audio.tts.voice ?? '';
		} else {
			voice = $config.audio.tts.voice ?? '';
		}

		nonLocalVoices = $settings.audio?.tts?.nonLocalVoices ?? false;

		await getVoices();
	});

	$: if (TTSEngine && TTSEngineConfig) {
		onTTSEngineChange();
	}

	const onTTSEngineChange = async () => {
		if (TTSEngine === 'browser-kokoro') {
			await loadKokoro();
		}
	};

	const loadKokoro = async () => {
		if (TTSEngine === 'browser-kokoro') {
			voices = [];

			if (TTSEngineConfig?.dtype) {
				TTSModel = null;
				TTSModelProgress = null;
				TTSModelLoading = true;

				const model_id = 'onnx-community/Kokoro-82M-v1.0-ONNX';

				const { KokoroTTS } = await import('kokoro-js');
				TTSModel = await KokoroTTS.from_pretrained(model_id, {
					dtype: TTSEngineConfig.dtype, // Options: "fp32", "fp16", "q8", "q4", "q4f16"
					device: !!navigator?.gpu ? 'webgpu' : 'wasm', // Detect WebGPU
					progress_callback: (e) => {
						TTSModelProgress = e;
						console.log(e);
					}
				});

				await getVoices();

				// const rawAudio = await tts.generate(inputText, {
				// 	// Use `tts.list_voices()` to list all available voices
				// 	voice: voice
				// });

				// const blobUrl = URL.createObjectURL(await rawAudio.toBlob());
				// const audio = new Audio(blobUrl);

				// audio.play();
			}
		}
	};
</script>

<form
	id="tab-audio"
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		saveSettings({
			audio: {
				stt: {
					engine: STTEngine !== '' ? STTEngine : undefined,
					language: STTLanguage !== '' ? STTLanguage : undefined
				},
				tts: {
					engine: TTSEngine !== '' ? TTSEngine : undefined,
					engineConfig: TTSEngineConfig,
					playbackRate: playbackRate,
					voice: voice !== '' ? voice : undefined,
					defaultVoice: $config?.audio?.tts?.voice ?? '',
					nonLocalVoices: $config.audio.tts.engine === '' ? nonLocalVoices : undefined
				}
			}
		});
		dispatch('save');
	}}
>
	<div class=" space-y-3 overflow-y-scroll max-h-[28rem] md:max-h-full">
		<div>
			<div class=" mb-1 text-sm font-medium">{$i18n.t('STT Settings')}</div>

			{#if $config.audio.stt.engine !== 'web'}
				<div class=" py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Speech-to-Text Engine')}</div>
					<div class="flex items-center relative">
						<select
							class="w-fit pr-8 rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
							bind:value={STTEngine}
							aria-label={$i18n.t('Speech-to-Text Engine')}
							placeholder={$i18n.t('Select an engine')}
						>
							<option value="">{$i18n.t('Default')}</option>
							<option value="web">{$i18n.t('Web API')}</option>
						</select>
					</div>
				</div>

				<div class=" py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Language')}</div>

					<div class="flex items-center relative text-xs px-3">
						<Tooltip
							content={$i18n.t(
								'The language of the input audio. Supplying the input language in ISO-639-1 (e.g. en) format will improve accuracy and latency. Leave blank to automatically detect the language.'
							)}
							placement="top"
						>
							<input
								type="text"
								bind:value={STTLanguage}
								aria-label={$i18n.t('Speech-to-Text Language')}
								placeholder={$i18n.t('e.g. en')}
								class=" text-sm text-right bg-transparent dark:text-gray-300 outline-hidden"
							/>
						</Tooltip>
					</div>
				</div>
			{/if}

			<div class=" py-0.5 flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Instant Auto-Send After Voice Transcription')}
				</div>

				<button
					class="p-1 px-3 text-xs flex rounded-sm transition"
					on:click={() => {
						toggleSpeechAutoSend();
					}}
					type="button"
					role="switch"
					aria-checked={speechAutoSend}
				>
					{#if speechAutoSend === true}
						<span class="ml-2 self-center">{$i18n.t('On')}</span>
					{:else}
						<span class="ml-2 self-center">{$i18n.t('Off')}</span>
					{/if}
				</button>
			</div>
		</div>

		<div>
			<div class=" mb-1 text-sm font-medium">{$i18n.t('TTS Settings')}</div>

			<div class=" py-0.5 flex w-full justify-between">
				<div class=" self-center text-xs font-medium">{$i18n.t('Text-to-Speech Engine')}</div>
				<div class="flex items-center relative">				
					<select
						class="w-fit pr-8 rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
						bind:value={TTSEngine}
						aria-label={$i18n.t('Text-to-Speech Engine')}
						placeholder={$i18n.t('Select an engine')}
					>
						<option value="">{$i18n.t('Default')}</option>
						<option value="browser-kokoro">{$i18n.t('Kokoro.js (Browser)')}</option>
					</select>
				</div>
			</div>

			{#if TTSEngine === 'browser-kokoro'}
				<div class=" py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Kokoro.js Dtype')}</div>
					<div class="flex items-center relative">
						<select
							class="w-fit pr-8 rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
							bind:value={TTSEngineConfig.dtype}
							aria-label={$i18n.t('Kokoro.js Dtype')}
							placeholder={$i18n.t('Select dtype')}
						>
							<option value="" disabled selected>{$i18n.t('Select dtype')}</option>
							<option value="fp32">fp32</option>
							<option value="fp16">fp16</option>
							<option value="q8">q8</option>
							<option value="q4">q4</option>
						</select>
					</div>
				</div>
			{/if}

			<div class=" py-0.5 flex w-full justify-between">
				<div class=" self-center text-xs font-medium">{$i18n.t('Auto-playback response')}</div>

				<button
					class="p-1 px-3 text-xs flex rounded-sm transition"
					on:click={() => {
						toggleResponseAutoPlayback();
					}}
					type="button"
					role="switch"
					aria-checked={responseAutoPlayback}
				>
					{#if responseAutoPlayback === true}
						<span class="ml-2 self-center">{$i18n.t('On')}</span>
					{:else}
						<span class="ml-2 self-center">{$i18n.t('Off')}</span>
					{/if}
				</button>
			</div>

			<div class=" py-0.5 flex w-full justify-between">
				<div class=" self-center text-xs font-medium">{$i18n.t('Speech Playback Speed')}</div>

				<div class="flex items-center relative text-xs px-3">
					<input
						type="number"
						min="0"
						step="0.01"
						bind:value={playbackRate}
						aria-label={$i18n.t('Speech Playback Speed')}
						class=" text-sm text-right bg-transparent dark:text-gray-300 outline-hidden"
					/>
					x
				</div>
			</div>
		</div>

		<hr class=" border-gray-100/30 dark:border-gray-850/30" />

		{#if TTSEngine === 'browser-kokoro'}
			{#if TTSModel}
				<div>
					<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Voice')}</div>
					<div class="flex w-full">
						<div class="flex-1">
							<input
								list="voice-list"
								class="w-full text-sm bg-transparent dark:text-gray-300 outline-hidden"
								bind:value={voice}
								aria-label={$i18n.t('Voice')}
								placeholder={$i18n.t('Select a voice')}
							/>

							<datalist id="voice-list">
								{#each voices as _voice}
									<option value={_voice.id}>
										{_voice.name}{_voice.description ? ` - ${_voice.description}` : ''}
									</option>
								{/each}
							</datalist>
						</div>
					</div>
				</div>
			{:else}
				<div>
					<div class=" mb-2.5 text-sm font-medium flex gap-2 items-center">
						<Spinner className="size-4" />

						<div class=" text-sm font-medium shimmer">
							{$i18n.t('Loading Kokoro.js...')}
							{TTSModelProgress && TTSModelProgress.status === 'progress'
								? `(${Math.round(TTSModelProgress.progress * 10) / 10}%)`
								: ''}
						</div>
					</div>

					<div class="text-xs text-gray-500">
						{$i18n.t('Please do not close the settings page while loading the model.')}
					</div>
				</div>
			{/if}
		{:else if $config.audio.tts.engine === ''}
			<div>
				<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Voice')}</div>
				<div class="flex w-full">
					<div class="flex-1">
						<select
							class="w-full text-sm bg-transparent dark:text-gray-300 outline-hidden"
							bind:value={voice}
							aria-label={$i18n.t('Voice')}
						>
							<option value="" selected={voice !== ''}>{$i18n.t('Default')}</option>
							{#each voices.filter((v) => nonLocalVoices || v.localService === true) as _voice}
								<option
									value={_voice.name}
									class="bg-gray-100 dark:bg-gray-700"
									selected={voice === _voice.name}>{_voice.name}</option
								>
							{/each}
						</select>
					</div>
				</div>
				<div class="flex items-center justify-between my-1.5">
					<div class="text-xs">
						{$i18n.t('Allow non-local voices')}
					</div>

					<div class="mt-1">
						<Switch bind:state={nonLocalVoices} />
					</div>
				</div>
			</div>
		{:else if $config.audio.tts.engine !== ''}
			<div>
				<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Voice')}</div>
				<div class="flex w-full">
					<div class="flex-1">
						{#if voices.length > 0}
							<select
								class="w-full text-sm bg-transparent dark:text-gray-300 outline-hidden"
								bind:value={voice}
								aria-label={$i18n.t('Voice')}
							>
								<option value="">{$i18n.t('Default')}</option>
								{#each voices as _voice}
									<option value={_voice.id} class="bg-gray-100 dark:bg-gray-700">
										{_voice.name}{_voice.description ? ` - ${_voice.description}` : ''}
									</option>
								{/each}
							</select>
						{:else}
							<input
								list="voice-list"
								class="w-full text-sm bg-transparent dark:text-gray-300 outline-hidden"
								bind:value={voice}
								aria-label={$i18n.t('Voice')}
								placeholder={$i18n.t('Select a voice')}
							/>
						{/if}
					</div>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
