// A button debouncer: a two-flop synchronizer feeding a saturating counter.

module debouncer (
    input  logic clk,
    input  logic button,
    output logic pressed
);
  logic sync0, sync1;
  logic [3:0] stable_for;

  always_ff @(posedge clk) begin
    sync0 <= button;
    sync1 <= sync0;
  end

  always_ff @(posedge clk) begin
    if (sync1 != pressed) stable_for <= stable_for + 1'b1;
    else stable_for <= '0;
  end

  always_ff @(posedge clk) begin
    if (stable_for == 4'hF) pressed <= sync1;
  end
endmodule
