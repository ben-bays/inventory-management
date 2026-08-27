<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="candidates.length === 0" class="card empty-state">
      {{ t('restocking.recommendations.noCandidates') }}
    </div>
    <div v-else>
      <div v-if="placedOrder" class="success-banner">
        <span>
          {{ t('restocking.orderPlaced', {
            orderNumber: placedOrder.order_number,
            date: formatDate(placedOrder.expected_delivery)
          }) }}
        </span>
        <router-link to="/orders" class="banner-link">{{ t('restocking.viewInOrders') }}</router-link>
      </div>

      <div class="card budget-card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget.title') }}</h3>
          <span class="budget-hint">
            {{ t('restocking.budget.fullCoverage', { amount: formatCurrency(fullCoverageCost) }) }}
          </span>
        </div>

        <div class="budget-body">
          <div class="budget-amount">{{ formatCurrency(budget) }}</div>
          <input
            v-model.number="budget"
            type="range"
            class="budget-slider"
            :min="0"
            :max="budgetMax"
            :step="budgetStep"
            :aria-label="t('restocking.budget.label')"
          />
          <div class="slider-scale">
            <span>{{ formatCurrency(0) }}</span>
            <span>{{ formatCurrency(budgetMax) }}</span>
          </div>
        </div>

        <div class="stats-grid budget-stats">
          <div class="stat-card">
            <div class="stat-label">{{ t('restocking.budget.allocated') }}</div>
            <div class="stat-value">{{ formatCurrency(allocatedCost) }}</div>
          </div>
          <div :class="['stat-card', isOverBudget ? 'danger' : 'success']">
            <div class="stat-label">{{ t('restocking.budget.remaining') }}</div>
            <div class="stat-value">{{ remainingDisplay }}</div>
          </div>
          <div class="stat-card info">
            <div class="stat-label">{{ t('restocking.recommendations.coverage') }}</div>
            <div class="stat-value">{{ coveragePercent }}%</div>
          </div>
        </div>

        <div v-if="isOverBudget" class="over-budget-warning">
          {{ t('restocking.budget.overBudget', { amount: formatCurrency(-remainingBudget) }) }}
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">
              {{ t('restocking.recommendations.title') }}
              ({{ t('restocking.recommendations.itemsSelected', { count: cart.length }) }})
            </h3>
            <p class="card-subtitle">{{ t('restocking.recommendations.subtitle') }}</p>
          </div>
          <button v-if="isCustomized" class="btn-secondary" @click="resetCart">
            {{ t('restocking.recommendations.reset') }}
          </button>
        </div>

        <div v-if="cart.length === 0" class="empty-state">
          {{ t('restocking.recommendations.empty') }}
        </div>
        <div v-else class="table-container">
          <table class="restock-table">
            <thead>
              <tr>
                <th class="col-sku">{{ t('restocking.table.sku') }}</th>
                <th class="col-item">{{ t('restocking.table.item') }}</th>
                <th class="col-num">{{ t('restocking.table.forecast') }}</th>
                <th class="col-num">{{ t('restocking.table.onHand') }}</th>
                <th class="col-num">{{ t('restocking.table.shortfall') }}</th>
                <th class="col-num">{{ t('restocking.table.unitCost') }}</th>
                <th class="col-qty">{{ t('restocking.table.quantity') }}</th>
                <th class="col-num">{{ t('restocking.table.lineTotal') }}</th>
                <th class="col-lead">{{ t('restocking.table.leadTime') }}</th>
                <th class="col-actions"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="line in cart" :key="line.sku">
                <td class="col-sku"><strong>{{ line.sku }}</strong></td>
                <td class="col-item">{{ translateProductName(line.name) }}</td>
                <td class="col-num">{{ line.forecasted_demand.toLocaleString() }}</td>
                <td class="col-num">{{ line.quantity_on_hand.toLocaleString() }}</td>
                <td class="col-num shortfall">{{ line.shortfall.toLocaleString() }}</td>
                <td class="col-num">{{ formatCurrency(line.unit_cost, 2) }}</td>
                <td class="col-qty">
                  <input
                    type="number"
                    class="qty-input"
                    :value="line.quantity"
                    :min="1"
                    :max="line.shortfall"
                    :aria-label="`${t('restocking.table.quantity')} ${line.sku}`"
                    @input="setQuantity(line.sku, $event.target.value, line.shortfall)"
                  />
                  <span v-if="line.quantity < line.shortfall" class="partial-tag">
                    {{ Math.round((line.quantity / line.shortfall) * 100) }}%
                  </span>
                </td>
                <td class="col-num"><strong>{{ formatCurrency(line.lineTotal, 2) }}</strong></td>
                <td class="col-lead">
                  {{ t('orders.submitted.leadTimeDays', { days: line.lead_time_days }) }}
                </td>
                <td class="col-actions">
                  <button class="btn-remove" @click="removeItem(line.sku)">
                    {{ t('restocking.remove') }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="order-footer">
          <div v-if="submitError" class="submit-error">{{ submitError }}</div>
          <button
            class="btn-primary"
            :disabled="cart.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

const BUDGET_STEP = 500

export default {
  name: 'Restocking',
  setup() {
    const { t, currentLocale, currentCurrency, translateProductName } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const submitting = ref(false)
    const submitError = ref(null)
    const placedOrder = ref(null)

    // Raw data
    const candidates = ref([])

    // User controls
    const budget = ref(0)
    const quantityOverrides = ref({})
    const removedSkus = ref([])

    const currencySymbol = computed(() => (currentCurrency.value === 'JPY' ? '¥' : '$'))

    const fullCoverageCost = computed(() =>
      candidates.value.reduce((sum, c) => sum + c.full_coverage_cost, 0)
    )

    // Round the slider ceiling up to a clean multiple above full coverage
    const budgetMax = computed(() => {
      const ceiling = Math.ceil(fullCoverageCost.value / 5000) * 5000
      return Math.max(ceiling, 5000)
    })

    // Greedy fill: worst shortfall first, each item capped at its shortfall,
    // until the budget can no longer afford a single unit of the next item.
    const recommendedCart = computed(() => {
      let remaining = budget.value
      const lines = []

      for (const candidate of candidates.value) {
        if (removedSkus.value.includes(candidate.sku)) continue

        const affordable = Math.floor(remaining / candidate.unit_cost)
        const quantity = Math.min(candidate.shortfall, affordable)
        if (quantity <= 0) continue

        remaining -= quantity * candidate.unit_cost
        lines.push({ ...candidate, quantity })
      }

      return lines
    })

    // Apply the user's manual quantity edits on top of the recommendation
    const cart = computed(() =>
      recommendedCart.value.map(line => {
        const override = quantityOverrides.value[line.sku]
        const quantity = override === undefined ? line.quantity : override
        return {
          ...line,
          quantity,
          lineTotal: Math.round(quantity * line.unit_cost * 100) / 100
        }
      })
    )

    const allocatedCost = computed(() =>
      Math.round(cart.value.reduce((sum, line) => sum + line.lineTotal, 0) * 100) / 100
    )

    const remainingBudget = computed(() =>
      Math.round((budget.value - allocatedCost.value) * 100) / 100
    )

    const isOverBudget = computed(() => remainingBudget.value < 0)

    // Keep the minus sign outside the currency symbol so an overrun reads as a deficit
    const remainingDisplay = computed(() => {
      const formatted = formatCurrency(Math.abs(remainingBudget.value))
      return isOverBudget.value ? `-${formatted}` : formatted
    })

    const coveragePercent = computed(() => {
      const totalShortfall = candidates.value.reduce((sum, c) => sum + c.shortfall, 0)
      if (totalShortfall === 0) return 0
      const ordered = cart.value.reduce((sum, line) => sum + line.quantity, 0)
      return Math.round((ordered / totalShortfall) * 100)
    })

    const isCustomized = computed(
      () => removedSkus.value.length > 0 || Object.keys(quantityOverrides.value).length > 0
    )

    const loadCandidates = async () => {
      try {
        loading.value = true
        error.value = null
        candidates.value = await api.getRestockCandidates()

        // Open at half of full coverage so the slider starts somewhere useful
        const half = fullCoverageCost.value / 2
        budget.value = Math.min(
          Math.round(half / BUDGET_STEP) * BUDGET_STEP,
          budgetMax.value
        )
      } catch (err) {
        error.value = 'Failed to load restock recommendations: ' + err.message
      } finally {
        loading.value = false
      }
    }

    const setQuantity = (sku, rawValue, shortfall) => {
      const parsed = parseInt(rawValue, 10)
      if (isNaN(parsed)) return
      quantityOverrides.value = {
        ...quantityOverrides.value,
        [sku]: Math.min(Math.max(parsed, 1), shortfall)
      }
    }

    const removeItem = (sku) => {
      removedSkus.value = [...removedSkus.value, sku]
      const { [sku]: _removed, ...rest } = quantityOverrides.value
      quantityOverrides.value = rest
    }

    const resetCart = () => {
      removedSkus.value = []
      quantityOverrides.value = {}
    }

    const placeOrder = async () => {
      try {
        submitting.value = true
        submitError.value = null
        placedOrder.value = await api.createRestockOrder({
          budget: budget.value,
          items: cart.value.map(line => ({ sku: line.sku, quantity: line.quantity }))
        })
      } catch (err) {
        submitError.value =
          err.response?.data?.detail || `${t('restocking.submitFailed')}: ${err.message}`
      } finally {
        submitting.value = false
      }
    }

    // A new budget means a new proposal - clear the previous confirmation
    watch(budget, () => {
      placedOrder.value = null
      submitError.value = null
    })

    const formatCurrency = (value, decimals = 0) => {
      const amount = Number(value) || 0
      return `${currencySymbol.value}${amount.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
      })}`
    }

    const formatDate = (dateString) => {
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return '-'
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' })
    }

    onMounted(loadCandidates)

    return {
      t,
      loading,
      error,
      submitting,
      submitError,
      placedOrder,
      candidates,
      budget,
      budgetMax,
      budgetStep: BUDGET_STEP,
      fullCoverageCost,
      cart,
      allocatedCost,
      remainingBudget,
      remainingDisplay,
      isOverBudget,
      coveragePercent,
      isCustomized,
      setQuantity,
      removeItem,
      resetCart,
      placeOrder,
      formatCurrency,
      formatDate,
      translateProductName
    }
  }
}
</script>

<style scoped>
.card-subtitle {
  margin: 0.25rem 0 0;
  font-size: 0.813rem;
  color: #64748b;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
}

/* Success banner */
.success-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  background: #d1fae5;
  border: 1px solid #6ee7b7;
  color: #065f46;
  border-radius: 8px;
  padding: 0.875rem 1.25rem;
  margin-bottom: 1.5rem;
  font-size: 0.875rem;
  font-weight: 500;
}

.banner-link {
  color: #065f46;
  font-weight: 600;
  text-decoration: underline;
  white-space: nowrap;
}

/* Budget slider */
.budget-card {
  padding-bottom: 1.5rem;
}

.budget-hint {
  font-size: 0.813rem;
  color: #64748b;
}

.budget-body {
  padding: 1.5rem 1.5rem 0.5rem;
}

.budget-amount {
  font-size: 2.5rem;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 1rem;
  font-variant-numeric: tabular-nums;
}

.budget-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  outline: none;
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #0f172a;
  border: 3px solid white;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
  cursor: pointer;
}

.budget-slider::-moz-range-thumb {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #0f172a;
  border: 3px solid white;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
  cursor: pointer;
}

.budget-slider:focus-visible {
  box-shadow: 0 0 0 3px rgba(15, 23, 42, 0.15);
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  font-size: 0.75rem;
  color: #94a3b8;
}

.budget-stats {
  padding: 0 1.5rem;
  margin-bottom: 0;
}

.over-budget-warning {
  margin: 1rem 1.5rem 0;
  padding: 0.75rem 1rem;
  background: #fee2e2;
  border: 1px solid #fca5a5;
  border-radius: 6px;
  color: #991b1b;
  font-size: 0.875rem;
  font-weight: 500;
}

/* Recommendation table */
.restock-table {
  table-layout: fixed;
  width: 100%;
}

.col-sku {
  width: 100px;
}

.col-item {
  width: 200px;
}

.col-num {
  width: 100px;
  text-align: right;
}

.col-qty {
  width: 130px;
}

.col-lead {
  width: 100px;
}

.col-actions {
  width: 90px;
}

.shortfall {
  color: #dc2626;
  font-weight: 600;
}

.qty-input {
  width: 80px;
  padding: 0.375rem 0.5rem;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 0.875rem;
  font-family: inherit;
  color: #0f172a;
  text-align: right;
}

.qty-input:focus {
  outline: none;
  border-color: #0f172a;
  box-shadow: 0 0 0 3px rgba(15, 23, 42, 0.1);
}

.partial-tag {
  margin-left: 0.5rem;
  font-size: 0.75rem;
  color: #b45309;
  font-weight: 600;
}

/* Buttons */
.order-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 1rem;
  padding: 1.25rem 1.5rem;
  border-top: 1px solid #e2e8f0;
}

.submit-error {
  color: #dc2626;
  font-size: 0.875rem;
  font-weight: 500;
}

.btn-primary {
  background: #0f172a;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 0.625rem 1.5rem;
  font-size: 0.875rem;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-primary:hover:not(:disabled) {
  background: #1e293b;
}

.btn-primary:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.btn-secondary {
  background: white;
  color: #475569;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 0.375rem 0.875rem;
  font-size: 0.813rem;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-secondary:hover {
  background: #f8fafc;
  border-color: #94a3b8;
}

.btn-remove {
  background: none;
  border: none;
  color: #64748b;
  font-size: 0.813rem;
  font-family: inherit;
  cursor: pointer;
  padding: 0.25rem;
}

.btn-remove:hover {
  color: #dc2626;
  text-decoration: underline;
}
</style>
