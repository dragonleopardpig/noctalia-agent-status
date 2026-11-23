--!nonstrict
local current = { status = "idle", counts = {} }
local labels = { idle = "AI", working = "Working", waiting = "Waiting", done = "Done", error = "Error" }
local glyphs = { idle = "sparkles", working = "loader", waiting = "message-question", done = "circle-check", error = "alert-circle" }
-- Its own colour (settings.nix, an empty colour) unless it waits for you or failed.
local colors = { idle = "", working = "", waiting = "tertiary", done = "", error = "error" }
local failureDescriptions = {
  quota = "Model quota reached",
  rate_limit = "Model rate limit reached",
  sign_in = "Sign-in required",
  access_denied = "Access denied by the model provider",
  unavailable = "Model service is unavailable",
  timeout = "Model request timed out",
  step_limit = "Agent reached its step limit",
  token_limit = "Agent reached its token limit",
  interrupted = "Agent session ended during a request",
  launch = "Antigravity could not start",
  request_failed = "Last request failed",
}

local function render()
  local state = labels[current.status] and current.status or "idle"
  local counts = current.counts or {}
  local failure = state == "error" and (current.failure or {}) or nil
  local description = state == "idle" and "Ready" or labels[state]
  if failure then
    description = failureDescriptions[failure.kind] or failureDescriptions.request_failed
  end
  local tooltip = "Antigravity — " .. description
  if current.workspace and current.workspace ~= "" then
    tooltip ..= "\n" .. current.workspace
  end
  if (counts.working or 0) + (counts.waiting or 0) > 1 then
    tooltip ..= "\n" .. tostring(counts.working or 0) .. " working, " .. tostring(counts.waiting or 0) .. " waiting"
  end
  if failure then
    if failure.retry_time then
      if failure.retry_passed then
        tooltip ..= "\nReported reset time passed · " .. failure.retry_time
      else
        tooltip ..= "\nReported retry time · " .. failure.retry_time
      end
    end
    if failure.reported_at then
      tooltip ..= "\nLast update · " .. failure.reported_at
    end
  end
  tooltip ..= "\nClick to show/hide agent · Super+C"
  barWidget.setTooltip(tooltip)
  barWidget.setGlyph(glyphs[state])
  barWidget.setText(labels[state])
  barWidget.setColor(colors[state])
  barWidget.setGlyphColor(colors[state])
end

function onClick()
  noctalia.runAsync({ "umbriel", "msg", "scratchpad-toggle:agent" })
end

function onConfigChanged()
  render()
end

render()
local executable = noctalia.expandPath("~/.local/bin/noctalia-agent-status")
local command = "'" .. string.gsub(executable, "'", "'\\''") .. "' watch"
noctalia.runStream(command, function(line)
  local data = noctalia.json.decode(line)
  if type(data) == "table" then
    current = data
    render()
  end
end)
