<template>
  <div class="ai glass" :class="{ collapsed }">
    <div class="ai-head">
      <span @click="collapsed = !collapsed" style="cursor:pointer;flex:1">🤖 AI 空间分析助手</span>
      <button class="mini" @click="reset" title="新会话">↺</button>
      <span class="toggle" @click="collapsed = !collapsed">{{ collapsed ? '▲' : '▼' }}</span>
    </div>

    <div v-show="!collapsed" class="ai-body">
      <div class="msgs" ref="msgs">
        <div v-if="!messages.length" class="welcome">
          用自然语言描述分析需求，AI 会自动调用对应的空间分析并在地图上联动高亮。支持多轮追问。
          <div class="chips">
            <span class="chip" v-for="s in samples" :key="s" @click="send(s)">{{ s }}</span>
          </div>
        </div>

        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <div class="bubble">
            <div>{{ m.text }}</div>
            <div v-if="m.tool" class="meta">
              <span class="tag">{{ m.engine === 'mock' ? 'Mock' : 'DeepSeek' }} · {{ m.tool }}</span>
              <a class="steps-toggle" @click="m.open = !m.open">{{ m.open ? '收起过程' : '查看执行过程' }}</a>
            </div>
            <ol v-if="m.open && m.steps" class="steps">
              <li v-for="(s, j) in m.steps" :key="j">{{ s }}</li>
            </ol>
          </div>
        </div>

        <div v-if="loading" class="msg assistant">
          <div class="bubble running">
            <i class="spinner"></i>
            <span>{{ runHint }}</span>
          </div>
        </div>
      </div>

      <div class="ai-input">
        <input type="text" v-model="text" placeholder="例如：评估主城区地段价值；再把水位调到8米"
          @keyup.enter="send()" :disabled="loading" />
        <button class="btn sm primary" @click="send()" :disabled="loading">发送</button>
      </div>
    </div>
  </div>
</template>

<script>
import { api } from '../api'

const RUN_HINTS = ['理解需求与上下文…', '选择空间分析工具…', '执行分析引擎…', '组织回答并联动地图…']

export default {
  name: 'AIChat',
  props: { city: String, contextPoint: Array },
  emits: ['result'],
  data() {
    return {
      collapsed: false, text: '', loading: false, messages: [],
      runHint: RUN_HINTS[0], _hintTimer: null,
      session: 'sess-' + Math.random().toString(36).slice(2, 9),
      samples: ['评估当前研究区地段价值', '帮我选址，筛选高价值地块', '分析价值的热点聚集区', '从我选的点规划撤离路线', '模拟6米水位淹没']
    }
  },
  methods: {
    startHints() {
      let i = 0; this.runHint = RUN_HINTS[0]
      this._hintTimer = setInterval(() => { i = (i + 1) % RUN_HINTS.length; this.runHint = RUN_HINTS[i] }, 1200)
    },
    stopHints() { clearInterval(this._hintTimer) },
    async send(preset) {
      const q = (preset || this.text).trim()
      if (!q || this.loading) return
      this.text = ''
      this.messages.push({ role: 'user', text: q })
      this.scroll(); this.loading = true; this.startHints()
      try {
        const r = await api.ai(q, this.city, this.contextPoint, this.session)
        this.messages.push({ role: 'assistant', text: r.reply || '已完成分析。',
          tool: r.tool, engine: r.engine, steps: r.steps, open: false })
        this.$emit('result', r)
      } catch (e) {
        this.messages.push({ role: 'assistant', text: '出错了：' + (e?.response?.data?.detail || e.message) })
      } finally { this.loading = false; this.stopHints(); this.scroll() }
    },
    async reset() {
      try { await api.aiReset(this.session) } catch (e) { /* 忽略 */ }
      this.messages = []
      this.flashReset = true
    },
    scroll() { this.$nextTick(() => { const el = this.$refs.msgs; if (el) el.scrollTop = el.scrollHeight }) }
  }
}
</script>

<style scoped>
.ai { position: absolute; bottom: 18px; right: 14px; width: 350px; z-index: 15;
  display: flex; flex-direction: column; overflow: hidden; transition: all .25s ease; }
.ai.collapsed { width: 230px; }
.ai-head { display: flex; align-items: center; gap: 8px; padding: 12px 16px;
  font-size: 14px; font-weight: 600; border-bottom: 1px solid var(--border); }
.mini { background: transparent; border: 1px solid var(--border); color: var(--text-dim);
  border-radius: 8px; width: 24px; height: 24px; cursor: pointer; }
.mini:hover { color: var(--text); }
.toggle { color: var(--text-dim); font-size: 11px; cursor: pointer; }
.ai-body { display: flex; flex-direction: column; height: 380px; }
.msgs { flex: 1; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.welcome { font-size: 12px; color: var(--text-dim); line-height: 1.7; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.chip { font-size: 11px; padding: 5px 9px; border-radius: 999px; cursor: pointer;
  background: rgba(77,163,255,.1); border: 1px solid var(--border); color: var(--text); }
.chip:hover { background: rgba(77,163,255,.22); }
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.bubble { max-width: 82%; padding: 9px 12px; border-radius: 12px; font-size: 13px; line-height: 1.55; }
.msg.user .bubble { background: var(--accent); color: #06121f; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: rgba(255,255,255,.06); color: var(--text); border-bottom-left-radius: 4px; }
.running { display: flex; align-items: center; gap: 8px; color: var(--text-dim); }
.meta { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; gap: 8px; }
.steps-toggle { font-size: 11px; color: var(--accent); cursor: pointer; white-space: nowrap; }
.steps { margin: 8px 0 0; padding-left: 18px; font-size: 11px; color: var(--text-dim); line-height: 1.7; }
.ai-input { display: flex; gap: 8px; padding: 12px 14px; border-top: 1px solid var(--border); }
.ai-input input { flex: 1; }
.btn.primary { background: var(--accent); color: #06121f; font-weight: 600; border-color: var(--accent); }
</style>
