defmodule LLMDB.XAIDeprecationsTest do
  use ExUnit.Case, async: false

  alias LLMDB.{Model, Normalize, Provider}
  alias LLMDB.Sources.Local

  @local_dir "priv/llm_db/local"
  @deprecated_at "2026-05-07"
  @retires_at "2026-05-15T19:00:00Z"

  @expected_lifecycle %{
    "grok-4-1-fast-reasoning" => "grok-4.3",
    "grok-4-1-fast-non-reasoning" => "grok-4.3",
    "grok-4-fast-reasoning" => "grok-4.3",
    "grok-4-fast-non-reasoning" => "grok-4.3",
    "grok-4-0709" => "grok-4.3",
    "grok-code-fast-1" => "grok-build-0.1",
    "grok-3" => "grok-4.3",
    "grok-imagine-image-pro" => "grok-imagine-image-quality"
  }

  test "local xAI overrides codify the 2026-05-15 retirement batch" do
    models = xai_models()

    Enum.each(@expected_lifecycle, fn {model_id, replacement} ->
      model = Map.fetch!(models, model_id)

      assert model.lifecycle.status == "deprecated"
      assert model.lifecycle.deprecated_at == @deprecated_at
      assert model.lifecycle.retires_at == @retires_at
      assert model.lifecycle.replacement == replacement
      assert Model.lifecycle_status(model) == "deprecated"
      assert Model.effective_status(model, ~U[2026-05-15 18:59:59Z]) == "deprecated"
      assert Model.effective_status(model, ~U[2026-05-15 19:00:00Z]) == "retired"
    end)
  end

  test "Grok 4.3 metadata captures launch details from xAI announcement" do
    model = xai_models() |> Map.fetch!("grok-4.3")

    assert model.limits.context == 1_000_000
    assert model.cost.input == 1.25
    assert model.cost.output == 2.5
    assert model.capabilities.reasoning.enabled == true
    assert model.capabilities.tools.enabled == true
    assert get_in(model.extra, [:constraints, :reasoning_effort]) == "supported"

    assert get_in(model.extra, [:constraints, :reasoning_effort_values]) == [
             "low",
             "medium",
             "high"
           ]
  end

  test "local xAI replacement overlays point at documented active targets" do
    models = xai_models()
    replacement = Map.fetch!(models, "grok-4.3")
    image = Map.fetch!(models, "grok-imagine-image-quality")

    assert replacement.capabilities.reasoning.enabled == true
    assert replacement.limits.context == 1_000_000
    assert replacement.cost.input == 1.25
    assert replacement.cost.output == 2.5

    assert pricing_rate(image, "image.output.1k") == 0.05
    assert pricing_rate(image, "image.output.2k") == 0.07
  end

  test "local xAI provider defaults capture documented tool pricing aliases" do
    provider = xai_provider()
    components = Map.new(provider.pricing_defaults.components, &{&1.id, &1})

    assert components["tool.web_search"].rate == 5.0
    assert components["tool.x_search"].rate == 5.0
    assert components["tool.code_execution"].rate == 5.0
    assert components["tool.code_interpreter"].rate == 5.0
    assert components["tool.attachment_search"].rate == 10.0
    assert components["tool.collections_search"].rate == 2.5
    assert components["tool.file_search"].rate == 2.5
    refute Map.has_key?(components, "tool.document_search")
  end

  test "local xAI overrides capture docs-only Imagine video pricing" do
    video = xai_models() |> Map.fetch!("grok-imagine-video")

    assert pricing_rate(video, "image.input") == 0.002
    assert pricing_rate(video, "video.input.second") == 0.01
    assert pricing_rate(video, "video.output.480p.second") == 0.05
    assert pricing_rate(video, "video.output.720p.second") == 0.07
  end

  defp xai_provider do
    xai_local_data()
    |> Map.delete(:models)
    |> Map.put(:id, :xai)
    |> Provider.new!()
  end

  defp xai_models do
    xai_local_data().models
    |> Normalize.normalize_models()
    |> Enum.map(&Model.new!/1)
    |> Map.new(&{&1.id, &1})
  end

  defp xai_local_data do
    {:ok, data} = Local.load(%{dir: @local_dir})
    Map.fetch!(data, "xai")
  end

  defp pricing_rate(model, component_id) do
    model.pricing.components
    |> Enum.find(&(&1.id == component_id))
    |> Map.fetch!(:rate)
  end
end
