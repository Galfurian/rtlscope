// A UART transmitter: a baud counter, a bit counter and a shift register.

module uart_tx #(
    parameter int CLKS_PER_BIT = 4
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       send,
    input  logic [7:0] data,
    output logic       tx,
    output logic       busy
);
  logic [9:0] shifter;
  logic [3:0] bits_left;
  logic [2:0] baud;
  logic       tick;

  assign tick = baud == 3'(CLKS_PER_BIT - 1);
  assign busy = bits_left != 4'd0;
  assign tx   = busy ? shifter[0] : 1'b1;

  always_ff @(posedge clk) begin
    if (rst || !busy || tick) baud <= '0;
    else baud <= baud + 1'b1;
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      shifter   <= '1;
      bits_left <= '0;
    end else if (send && !busy) begin
      shifter   <= {1'b1, data, 1'b0};
      bits_left <= 4'd10;
    end else if (busy && tick) begin
      shifter   <= {1'b1, shifter[9:1]};
      bits_left <= bits_left - 1'b1;
    end
  end
endmodule
