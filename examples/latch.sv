// A transparent latch, and the clock gate built from one.

module latch (
    input  logic clk,
    input  logic en,
    output logic gated_clk
);
  logic en_latched;

  always_latch begin
    if (!clk) en_latched = en;
  end

  assign gated_clk = clk & en_latched;
endmodule
