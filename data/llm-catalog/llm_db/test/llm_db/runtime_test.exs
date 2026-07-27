defmodule LLMDB.RuntimeTest do
  use ExUnit.Case, async: true

  alias LLMDB.Runtime

  test "custom providers include pricing_defaults" do
    runtime =
      Runtime.compile(
        custom: %{
          test: [
            pricing_defaults: %{
              currency: "USD",
              components: [
                %{id: "tool.web_search", kind: "tool", unit: "call", per: 1000, rate: 10.0}
              ]
            }
          ]
        }
      )

    [provider] = runtime.custom.providers
    assert provider.pricing_defaults.currency == "USD"
    assert [%{id: "tool.web_search"}] = provider.pricing_defaults.components
  end
end
